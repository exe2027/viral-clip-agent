#!/usr/bin/env python3
"""Pruebas del loop de revalidación + backfill de SEO (2026-11-24,
auto-seo, SPEC-2026-11-24-auto-seo.md).

- Backfill exacto (R7): con seo_inputs guardados, el score
  reproducido es el del corte (misma función, mismas entradas).
- Backfill aproximado (R7b): sin seo_inputs, con hashtags del juego
  y visible=None; nunca por encima del exacto en gameplay.
- Revalidación (R8/R9): pendiente -> aprobado escribe el veredicto
  (sin preview_seen); pendiente -> pendiente no escribe nada.
- Rechazado (R10): nunca se toca.
- Tope + anti-atasco (R11): 10 por corrida; un intento de hace
  < 24 h se salta.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.revalidate_pending import (  # noqa: E402
    MAX_CLIPS_PER_RUN,
    run,
)
from clipagent.seo_score import score_all_platforms  # noqa: E402

PLATFORMS = ("youtube_shorts", "tiktok", "instagram_reels",
             "facebook_reels")


def _clip(clip_id, **over):
    base = {
        "id": clip_id,
        "title": "GTA 6: Rubius descubre los cromos",
        "seo_title": "GTA 6: Rubius descubre los cromos",
        "seo_description": "Rubius descubre los cromos. El mejor "
                           "momento del directo.",
        "seo_tags": ["GTA 6", "GTA VI", "clips GTA 6",
                     "GTA 6 gameplay", "Rockstar Games"],
        "thumb_text": "¡QUÉ BARBARIDAD!",
        "language": "es",
        "status": "en_carpeta",
        "created_at": 1795400000000 + clip_id * 1000,
        "viral": {},
    }
    base.update(over)
    return base


def _inputs():
    return {"hashtags": ["#GTA6", "#GTA6Clips", "#GTA",
                         "#RockstarGames"],
            "visible": "cams+gameplay",
            "game_verified": True,
            "keyword": "GTA 6",
            "lang": "es"}


class FakeBridge:
    """Puente falso: registra cada llamada del loop."""

    def __init__(self, clips=None, events=None):
        self.clips = list(clips or [])
        self.events = dict(events or {})
        self.patched = []
        self.downloaded = []
        self.tracked = []
        self.reported = []
        self.previews = []

    def list_clips(self):
        return list(self.clips)

    def list_clip_events(self, clip_id):
        return list(self.events.get(clip_id, []))

    def update_clip(self, clip_id, fields):
        self.patched.append((clip_id, fields))
        return {"ok": True}

    def download_video(self, clip_id, out_path):
        self.downloaded.append(clip_id)
        with open(out_path, "wb") as f:
            f.write(b"fake-mp4")
        return out_path

    def tracking_step(self, clip_id, step, message, estado="completado"):
        self.tracked.append((clip_id, step, estado))
        return {"ok": True}

    def report_event(self, kind, message):
        self.reported.append((kind, message))
        return {"ok": True}

    def preview_seen(self, clip_id):
        self.previews.append(clip_id)
        return {"ok": True}


def _review_aprobado():
    return {"decision": "aprobado", "pasa": True, "hard_pasa": True,
            "needs_rerender": False, "hard_failures": [], "motivo": "",
            "fallos": [],
            "scores": {"momento_comprensible": 8, "titulo_legible": 8,
                       "sin_barras_negras": 8, "webcam_visible": 8},
            "frames_n": 0, "model": "", "transcript_words": 50,
            "observe_only": True, "face": {"pasa": True},
            "template": {"pasa": True, "checks": {
                "dimensiones": {"pasa": True},
                "titulo_banner": {"pasa": True}}},
            "gemini": {"decision": "aprobado"}}


def _gate_aprobado():
    return {"decision": "aprobado", "pasa": True, "criterios": {},
            "fallos": [], "fallos_duros": [], "motivo": ""}


class TestBackfillSeo(unittest.TestCase):
    def test_exacto_reproduce_el_score_del_corte(self):
        clip = _clip(1, viral={"seo_inputs": _inputs(),
                               "quality_gate": _gate_aprobado()})
        bridge = FakeBridge([clip])
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(bridge, cfg={}, limit=10, workdir=tmp,
                          now_ms=1795500000000)
        self.assertEqual(summary["seo_rellenados"], 4)
        self.assertEqual(len(bridge.patched), 1)
        _cid, fields = bridge.patched[0]
        expected = score_all_platforms({
            "title": clip["seo_title"],
            "description": clip["seo_description"],
            "tags": clip["seo_tags"],
            "thumbnail_text": clip["thumb_text"],
            "hashtags": _inputs()["hashtags"],
            "game_verified": True,
            "visible": "cams+gameplay",
            "lang": "es",
            "keyword": "GTA 6",
        })
        self.assertEqual(fields["seo_scores"], {
            p: expected[p]["score"] for p in PLATFORMS})
        self.assertEqual(fields["viral"]["seo_source"],
                         "backfill_exacto")
        # Backfill de SEO no descarga el MP4 ni valida.
        self.assertEqual(bridge.downloaded, [])

    def test_aproximado_nunca_por_encima_del_exacto(self):
        clip = _clip(2, viral={"quality_gate": _gate_aprobado()})
        bridge = FakeBridge([clip])
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(bridge, cfg={}, limit=10, workdir=tmp,
                          now_ms=1795500000000)
        self.assertEqual(summary["seo_rellenados"], 4)
        _cid, fields = bridge.patched[0]
        self.assertEqual(fields["viral"]["seo_source"],
                         "backfill_aproximado")
        exact = score_all_platforms({
            "title": clip["seo_title"],
            "description": clip["seo_description"],
            "tags": clip["seo_tags"],
            "thumbnail_text": clip["thumb_text"],
            "hashtags": _inputs()["hashtags"],
            "game_verified": True,
            "visible": "cams+gameplay",
            "lang": "es",
            "keyword": "GTA 6",
        })
        for platform in PLATFORMS:
            self.assertLessEqual(fields["seo_scores"][platform],
                                 exact[platform]["score"])

    def test_solo_rellena_las_que_faltan(self):
        clip = _clip(3, viral={
            "seo_inputs": _inputs(),
            "quality_gate": _gate_aprobado(),
            "seo_scores": {"youtube_shorts": 94, "tiktok": 92}})
        bridge = FakeBridge([clip])
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(bridge, cfg={}, limit=10, workdir=tmp,
                          now_ms=1795500000000)
        self.assertEqual(summary["seo_rellenados"], 2)
        _cid, fields = bridge.patched[0]
        self.assertNotIn("youtube_shorts", fields["seo_scores"])
        self.assertNotIn("tiktok", fields["seo_scores"])
        self.assertIn("instagram_reels", fields["seo_scores"])
        self.assertIn("facebook_reels", fields["seo_scores"])


class TestRevalidate(unittest.TestCase):
    def _clip_pendiente(self, clip_id=1):
        return _clip(clip_id, viral={
            "seo_scores": {p: 90 for p in PLATFORMS},
            "quality_gate": {"decision": "pendiente_revision",
                             "pasa": False, "criterios": {},
                             "fallos": [], "fallos_duros": [],
                             "motivo": "Gemini no disponible"}})

    def test_pendiente_a_aprobado_escribe_veredicto(self):
        clip = self._clip_pendiente()
        bridge = FakeBridge([clip])
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(
                bridge, cfg={}, limit=10, workdir=tmp,
                now_ms=1795500000000,
                validate_fn=lambda *a, **k: _review_aprobado(),
                transcribe_fn=lambda *a, **k: None)
        self.assertEqual(summary["veredictos"], 1)
        self.assertEqual(len(bridge.patched), 1)
        _cid, fields = bridge.patched[0]
        gate = fields["viral"]["quality_gate"]
        self.assertEqual(gate["decision"], "aprobado")
        self.assertIs(gate["pasa"], True)
        # El gate pasa por el veredicto solo: sin preview_seen.
        self.assertEqual(bridge.previews, [])
        self.assertEqual(bridge.downloaded, [1])

    def test_pendiente_a_pendiente_no_escribe(self):
        clip = self._clip_pendiente()
        bridge = FakeBridge([clip])
        review = dict(_review_aprobado())
        review["decision"] = "pendiente_revision"
        review["pasa"] = False
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(
                bridge, cfg={}, limit=10, workdir=tmp,
                now_ms=1795500000000,
                validate_fn=lambda *a, **k: review,
                transcribe_fn=lambda *a, **k: None)
        self.assertEqual(summary["veredictos"], 0)
        self.assertEqual(bridge.patched, [])
        # Queda marcado el intento (anti-atasco de 24 h).
        self.assertIn((1, "revalidado", "completado"),
                      bridge.tracked)

    def test_rechazado_no_se_toca(self):
        clip = _clip(1, viral={
            "seo_scores": {p: 90 for p in PLATFORMS},
            "quality_gate": {"decision": "rechazado", "pasa": False,
                             "criterios": {}, "fallos": ["audio"],
                             "fallos_duros": ["audio"], "motivo": ""}})
        bridge = FakeBridge([clip])
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(bridge, cfg={}, limit=10, workdir=tmp,
                          now_ms=1795500000000,
                          validate_fn=lambda *a, **k: _review_aprobado(),
                          transcribe_fn=lambda *a, **k: None)
        self.assertEqual(summary["revisados"], 0)
        self.assertEqual(bridge.patched, [])
        self.assertEqual(bridge.downloaded, [])

    def test_tope_10_y_anti_atasco_24h(self):
        clips = []
        for i in range(1, 13):
            clips.append(_clip(i, viral={
                "quality_gate": {"decision": "aprobado", "pasa": True,
                                 "criterios": {}, "fallos": [],
                                 "fallos_duros": [], "motivo": ""}}))
        events = {1: [{"step": "revalidado",
                       "at": 1795500000000 - 3600 * 1000}]}
        bridge = FakeBridge(clips, events=events)
        with tempfile.TemporaryDirectory() as tmp:
            summary = run(bridge, cfg={}, limit=MAX_CLIPS_PER_RUN,
                          workdir=tmp, now_ms=1795500000000,
                          validate_fn=lambda *a, **k: _review_aprobado(),
                          transcribe_fn=lambda *a, **k: None)
        self.assertEqual(summary["saltados"], 1)
        self.assertEqual(summary["revisados"], 10)
        self.assertEqual(summary["seo_rellenados"], 10 * 4)
        patched_ids = [cid for cid, _f in bridge.patched]
        self.assertNotIn(1, patched_ids)


class TestSeoInputsFromPackage(unittest.TestCase):
    def test_mapeo_igual_que_score_package_seo(self):
        from clipagent.metadata import seo_inputs_from_package
        pkg = {"best_title": "GTA 6: Rubius descubre los cromos",
               "game": "GTA 6", "visible": "cams+gameplay",
               "hashtags": ["#GTA6"],
               "platforms": {"youtube_shorts": {
                   "hashtags": ["#GTA6", "#GTA6Clips"]}}}
        inputs = seo_inputs_from_package(pkg, "es")
        self.assertEqual(inputs["hashtags"], ["#GTA6", "#GTA6Clips"])
        self.assertEqual(inputs["visible"], "cams+gameplay")
        self.assertIs(inputs["game_verified"], True)
        self.assertEqual(inputs["keyword"], "GTA 6")
        self.assertEqual(inputs["lang"], "es")

    def test_sin_hashtags_usa_los_del_paquete(self):
        from clipagent.metadata import seo_inputs_from_package
        inputs = seo_inputs_from_package(
            {"game": "GTA VI", "hashtags": ["#GTA6"]}, "en")
        self.assertEqual(inputs["hashtags"], ["#GTA6"])
        self.assertIs(inputs["game_verified"], True)
        self.assertEqual(inputs["lang"], "en")


if __name__ == "__main__":
    unittest.main()
