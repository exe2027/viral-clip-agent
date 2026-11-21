#!/usr/bin/env python3
"""Título Gemini de punta a punta (2026-11-19).

El título que se quema en el vídeo es el MISMO que va al panel
(title/seo_title) y a las plataformas: el del paquete Gemini
(best_title) cuando Gemini está, o el de reglas si no está (el
clip NUNCA muere porque falle Gemini). El texto de miniatura
viaja al panel (columna thumb_text).

- _publication_package: reutiliza el paquete de la revisión si
  trae best_title; si no, lo construye con pre_corte_package;
  si no se puede, {} (fallback a reglas).
- _enqueue_panel: el payload lleva title == seo_title == título
  quemado, y thumbnail_text cuando hay texto (sin texto no se
  manda la clave: el panel rechaza null con 400).
- process_peak: el título quemado (subtitle_clip) es el mismo
  que el del payload, con Gemini y sin él.
- burn_and_enqueue (VOD): thumbnail_text del paquete del
  manifiesto llega al payload.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.live_cutter import (
    LiveCutterSession,
    _publication_package,
)
from clipagent.vod_fallback import burn_and_enqueue

GEMINI_PKG = {
    "best_title": "Noni no puede parar de reírse en GTA 6",
    "description": "noni no puede contener las risas en GTA 6.",
    "tags": ["gta 6"], "hashtags": ["#GTA6"],
    "thumbnail_text": "RISAS SIN PARAR",
    "pkg_source": "gemini",
}

PRE_CORTE_FULL = {
    "best_title": "Noni no puede parar de reírse en GTA 6",
    "description_first_2_lines":
        "noni no puede contener las risas en GTA 6.",
    "tags": ["gta 6"], "hashtags": ["#GTA6"],
    "thumbnail_text": "RISAS SIN PARAR",
    "pkg_source": "gemini",
}

RULES_TITLE = "Título de reglas"


class TestPublicationPackage(unittest.TestCase):
    """_publication_package: el paquete que alimenta el quemado."""

    def test_reutiliza_paquete_de_la_revision(self):
        reviewed = {"package": dict(GEMINI_PKG)}
        with mock.patch("clipagent.workers.pre_corte_package") as pre:
            pkg = _publication_package(
                reviewed, None, 3220.0, 14.3, 1.09, {}, None,
                lang="es", streamer="noni", rec_start=3200.0,
                category="Grand Theft Auto VI", visible="gameplay")
        pre.assert_not_called()
        self.assertEqual(pkg["best_title"], GEMINI_PKG["best_title"])
        self.assertEqual(pkg["pkg_source"], "gemini")
        self.assertEqual(pkg["thumbnail_text"], "RISAS SIN PARAR")

    def test_construye_en_directo_con_visible_real(self):
        with mock.patch("clipagent.workers.pre_corte_package",
                        return_value=(dict(PRE_CORTE_FULL), {})
                        ) as pre:
            pkg = _publication_package(
                None, None, 3220.0, 14.3, 1.09, {}, None,
                lang="es", streamer="noni", rec_start=3200.0,
                category="Grand Theft Auto VI",
                visible="cams+gameplay")
        pre.assert_called_once()
        _args, kwargs = pre.call_args
        self.assertEqual(kwargs["visible"], "cams+gameplay")
        self.assertEqual(kwargs["lang"], "es")
        self.assertEqual(pkg["best_title"], GEMINI_PKG["best_title"])
        self.assertEqual(pkg["description"],
                         "noni no puede contener las risas en GTA 6.")
        self.assertEqual(pkg["thumbnail_text"], "RISAS SIN PARAR")
        self.assertEqual(pkg["pkg_source"], "gemini")

    def test_paquete_de_revision_sin_titulo_reconstruye(self):
        reviewed = {"package": {"description": "sin título"}}
        with mock.patch("clipagent.workers.pre_corte_package",
                        return_value=(dict(PRE_CORTE_FULL), {})):
            pkg = _publication_package(
                reviewed, None, 3220.0, 14.3, 1.09, {}, None,
                lang="es", streamer="noni", rec_start=3200.0,
                category="Grand Theft Auto VI", visible="gameplay")
        self.assertEqual(pkg["best_title"], GEMINI_PKG["best_title"])

    def test_sin_gemini_devuelve_vacio_y_no_levanta(self):
        with mock.patch("clipagent.workers.pre_corte_package",
                        side_effect=RuntimeError("sin Gemini")):
            pkg = _publication_package(
                None, None, 3220.0, 14.3, 1.09, {}, None,
                lang="es", streamer="noni", rec_start=3200.0,
                category="Grand Theft Auto VI", visible="gameplay")
        self.assertEqual(pkg, {})


class _FakeBridge:
    """Puente falso: registra el payload del panel."""

    def __init__(self):
        self.payloads = []

    def create_clip(self, payload):
        self.payloads.append(payload)
        return {"id": 99}

    def upload_video(self, clip_id, path):
        return {"ok": True}

    def tracking_step(self, *a, **k):
        return {"ok": True}

    def preview_seen(self, clip_id):
        return {"ok": True}

    def report_event(self, kind, message):
        return {"ok": True}


class TestEnqueuePanelTituloGemini(unittest.TestCase):
    """_enqueue_panel: title == seo_title == título quemado."""

    def _session(self, tmp, bridge):
        return LiveCutterSession(
            "noni", platform="twitch", stream_id="2026-11-19",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=bridge, dry_run=False, vod_id="123", lang="es")

    def _record(self, tmp, thumbnail_text="RISAS SIN PARAR"):
        pkg_path = os.path.join(tmp, "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({"priority": {"youtube_shorts": {"priority": 55}},
                       "description": "noni no puede contener las "
                                      "risas en GTA 6.",
                       "tags": ["gta 6"]}, f)
        record = {
            "clip_id": "live:noni:x:p3220", "clip": __file__,
            "title": GEMINI_PKG["best_title"],
            "start_sec": 3200, "end_sec": 3240,
            "peak_ts": 3220.0, "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6",
            "origin": "live", "viral_total": 52.9,
            "viral_auto": 52.9, "review": {},
            "streamer_hype": {"count": 0, "distinct": 0, "phrases": []},
            "subtitles_complete": True, "publish_blocked": False,
            "seo": {"youtube_shorts": 84, "tiktok": 91,
                    "instagram_reels": 88, "facebook_reels": 86},
            "publish_status": {}, "package_path": pkg_path,
            "hype_score": 5.6, "pkg_source": "gemini",
        }
        if thumbnail_text is not None:
            record["thumbnail_text"] = thumbnail_text
        return record

    def test_payload_lleva_titulo_gemini_y_miniatura(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _FakeBridge()
            session = self._session(tmp, bridge)
            panel_id = session._enqueue_panel(
                self._record(tmp))
            self.assertEqual(panel_id, 99)
            payload = bridge.payloads[0]
            self.assertEqual(payload["title"],
                             GEMINI_PKG["best_title"])
            self.assertEqual(payload["seo_title"],
                             GEMINI_PKG["best_title"])
            self.assertEqual(payload["thumbnail_text"],
                             "RISAS SIN PARAR")

    def test_sin_miniatura_no_manda_la_clave(self):
        # El panel rechaza thumbnail_text null con 400: sin texto
        # la clave no va en el payload.
        with tempfile.TemporaryDirectory() as tmp:
            bridge = _FakeBridge()
            session = self._session(tmp, bridge)
            session._enqueue_panel(self._record(tmp, None))
            self.assertNotIn("thumbnail_text", bridge.payloads[0])


class TestProcessPeakTituloGemini(unittest.TestCase):
    """process_peak: el título quemado es el del payload."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "clips")
        os.makedirs(self.out, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _session(self, bridge):
        class Gate:
            def check(self, ts, chat_path=None):
                return True, "Grand Theft Auto VI", "helix"

        class Recorder:
            def wait_until(self, rel_ts, timeout=120):
                return True

            def extract_window(self, start, duration, out_path):
                open(out_path, "wb").close()
                return None, 0.0

        return LiveCutterSession(
            "noni", platform="twitch", out_dir=self.out,
            recorder=Recorder(), gate=Gate(), bridge=bridge,
            dry_run=False, cfg={}, vod_id="123", lang="es")

    def _peak(self, ts=3220):
        detail = {"msgs": 100, "risas": 7, "hype": 4, "users": 50,
                  "msgs_per_10s": 134, "msgs_per_60s": 638,
                  "hype_score": 5.9, "hype_ratio": 1.31,
                  "burst_score": 5.9}
        return (float(ts), 14.3, 1.09, detail)

    def _aprobado(self):
        return {"decision": "aprobado", "pasa": True, "motivo": "",
                "fallos": [], "resumen": "", "scores": {},
                "frames_n": 3, "hard_pasa": True,
                "needs_rerender": False, "rerender_reason": "",
                "hard_failures": [], "observe_only": True,
                "face": {"pasa": True}, "template": {"pasa": True},
                "gemini": {"decision": "aprobado"}}

    def _patch_peak(self, pub_pkg):
        pkg_path = os.path.join(self.out, "pkg.json")
        sub_calls = {}

        def _sub(*a, **k):
            sub_calls.update(k)
            return {"publish_blocked": False, "no_speech": False,
                    "srt": None, "transcript": None}

        patches = [
            mock.patch("clipagent.detect.score_clip",
                       return_value={"total": 51.4}),
            mock.patch("clipagent.workers.cut_clip",
                       return_value={"layout": {}}),
            mock.patch("clipagent.workers.subtitle_clip",
                       side_effect=_sub),
            mock.patch("clipagent.live_cutter._publication_package",
                       return_value=dict(pub_pkg)),
            mock.patch("clipagent.live_cutter.build_title",
                       return_value=RULES_TITLE),
            mock.patch("clipagent.workers.package_clip_step",
                       return_value={"package_path": pkg_path}),
            mock.patch("clipagent.live_cutter.finalize_package",
                       return_value={
                           "seo": {"youtube_shorts": 92,
                                   "tiktok": 93,
                                   "instagram_reels": 91,
                                   "facebook_reels": 90},
                           "priority": {}}),
            mock.patch("clipagent.priority.publish_status",
                       return_value=("publicable", "ok")),
            mock.patch("clipagent.final_review.validate_final_clip",
                       return_value=self._aprobado()),
            mock.patch("clipagent.workers.enqueue_publish",
                       return_value={"id": 1}),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        return sub_calls

    def test_quema_el_titulo_gemini_y_lo_encola(self):
        bridge = _FakeBridge()
        session = self._session(bridge)
        sub_calls = self._patch_peak(dict(GEMINI_PKG))
        record = session.process_peak(self._peak())
        self.assertIsNotNone(record)
        # El quemado lleva el Gemini, no el de reglas.
        self.assertEqual(sub_calls.get("title"),
                         GEMINI_PKG["best_title"])
        # El record y el payload llevan el MISMO título quemado.
        self.assertEqual(record["title"], GEMINI_PKG["best_title"])
        self.assertEqual(record["burned_title"],
                         GEMINI_PKG["best_title"])
        self.assertEqual(record["pkg_source"], "gemini")
        self.assertEqual(len(bridge.payloads), 1)
        payload = bridge.payloads[0]
        self.assertEqual(payload["title"], GEMINI_PKG["best_title"])
        self.assertEqual(payload["seo_title"],
                         GEMINI_PKG["best_title"])
        self.assertEqual(payload["thumbnail_text"], "RISAS SIN PARAR")

    def test_sin_gemini_quema_el_de_reglas(self):
        bridge = _FakeBridge()
        session = self._session(bridge)
        sub_calls = self._patch_peak({})
        record = session.process_peak(self._peak())
        self.assertIsNotNone(record)
        self.assertEqual(sub_calls.get("title"), RULES_TITLE)
        self.assertEqual(record["title"], RULES_TITLE)
        self.assertEqual(record["burned_title"], RULES_TITLE)
        self.assertIsNone(record.get("pkg_source"))
        self.assertEqual(len(bridge.payloads), 1)
        payload = bridge.payloads[0]
        self.assertEqual(payload["title"], RULES_TITLE)
        self.assertEqual(payload["seo_title"], RULES_TITLE)
        self.assertNotIn("thumbnail_text", payload)


def _seo(score):
    return {p: {"score": score, "status": "ok", "can_publish": True}
            for p in ("youtube_shorts", "tiktok", "instagram_reels",
                      "facebook_reels")}


def _vod_clip(**over):
    base = {
        "n": 1, "file": "clips/clip_01.mp4", "peak": 3220.0,
        "start": 3200, "duration": 48,
        "viral": {"best_title": "GTA 6: Rubius descubre los cromos",
                  "game": "GTA VI",
                  "description": "Rubius descubre los cromos.",
                  "tags": ["#GTA6"],
                  "thumbnail_text": "CROMOS"},
        "viral_score": {"total": 53.8, "band": "alto",
                        "chat_multiple": 1.9},
        "seo_scores": _seo(94),
        "moment_card": {"game": "GTA VI"},
    }
    base.update(over)
    return base


class _VodBridge:
    def __init__(self):
        self.created = []
        self.uploaded = []
        self._next_id = 100

    def create_clip(self, payload):
        self.created.append(payload)
        self._next_id += 1
        return {"id": self._next_id}

    def upload_video(self, clip_id, video_path):
        self.uploaded.append((clip_id, video_path))
        return {"ok": True}


class TestBurnAndEnqueueMiniatura(unittest.TestCase):
    """burn_and_enqueue (VOD): thumbnail_text del paquete al panel."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.tmp.name, "clips"), exist_ok=True)
        self.video = os.path.join(self.tmp.name, "clips", "clip_01.mp4")
        with open(self.video, "wb") as f:
            f.write(b"fake-mp4")

    def tearDown(self):
        self.tmp.cleanup()

    def _patch(self):
        verdict = {"decision": "aprobado", "pasa": True,
                   "hard_pasa": True, "needs_rerender": False,
                   "rerender_reason": "", "hard_failures": [],
                   "motivo": "", "resumen": "aprobado",
                   "fallos": [], "scores": {}, "frames_n": 0,
                   "model": "", "transcript_words": 0,
                   "observe_only": True,
                   "face": {"pasa": True}, "template": {"pasa": True},
                   "gemini": {"decision": "aprobado"}}
        patches = [
            mock.patch("clipagent.workers.subtitle_clip",
                       return_value={"publish_blocked": False,
                                     "no_speech": False,
                                     "srt": self.video + ".srt",
                                     "burn": self.video,
                                     "transcript": None}),
            mock.patch("clipagent.final_review.validate_final_clip",
                       return_value=verdict),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])

    def test_manda_miniatura_del_paquete(self):
        self._patch()
        bridge = _VodBridge()
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name,
            _vod_clip())
        self.assertEqual(panel_id, 101, why)
        payload = bridge.created[0]
        self.assertEqual(payload["title"],
                         "GTA 6: Rubius descubre los cromos")
        self.assertEqual(payload["seo_title"],
                         "GTA 6: Rubius descubre los cromos")
        self.assertEqual(payload["thumbnail_text"], "CROMOS")

    def test_sin_miniatura_no_manda_la_clave(self):
        self._patch()
        bridge = _VodBridge()
        viral = {"best_title": "GTA 6: Rubius descubre los cromos",
                 "game": "GTA VI",
                 "description": "Rubius descubre los cromos.",
                 "tags": ["#GTA6"]}
        panel_id, why = burn_and_enqueue(
            bridge, "rubius", "2932415021", self.tmp.name,
            _vod_clip(viral=viral))
        self.assertEqual(panel_id, 101, why)
        self.assertNotIn("thumbnail_text", bridge.created[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
