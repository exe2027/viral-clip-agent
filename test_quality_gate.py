#!/usr/bin/env python3
"""Pruebas de la puerta de calidad B4 (2026-11-20).

El veredicto de validate_final_clip (cara + plantilla + Gemini
sobre el vídeo final) se convierte en 6 criterios obligatorios
antes de crear un job; un clip que falla no se encola como
publicable y, si los descartes del día superan el 30%, se alerta
una sola vez. Sin red, sin D1: el validador se simula con dicts.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.live_cutter import LiveCutterSession
from clipagent.quality_gate import (
    QUALITY_ALERT_MIN_SAMPLE,
    QUALITY_CRITERIA,
    QUALITY_DISCARD_ALERT_PCT,
    QualityDiscardTracker,
    build_quality_gate,
    publish_mode_for_quality,
)

ALL_SCORES = {
    "webcam_visible": 8.0,
    "titulo_legible": 8.0,
    "subtitulos_ok": 8.0,
    "sin_barras_negras": 8.0,
    "momento_comprensible": 8.0,
    "subtitulos_coherentes": 8.0,
    "plantilla": 8.0,
}


def _review(**over):
    base = {
        "decision": "aprobado",
        "pasa": True,
        "hard_pasa": True,
        "observe_only": True,
        "scores": dict(ALL_SCORES),
        "fallos": [],
        "motivo": "",
        "transcript_words": 12,
        "face": {"pasa": True, "method": "facecrop", "share": 1.0,
                 "motivo": ""},
        "template": {"pasa": True,
                     "checks": {
                         "dimensiones": {"pasa": True},
                         "titulo_banner": {"pasa": True},
                     }},
        "gemini": {"decision": "aprobado"},
    }
    base.update(over)
    return base


class TestBuildQualityGate(unittest.TestCase):
    def test_todo_pasa_aprobado(self):
        qg = build_quality_gate(_review(), thumbnail_text="EL GOL",
                                no_speech=False)
        self.assertEqual(qg["decision"], "aprobado")
        self.assertTrue(qg["pasa"])
        self.assertEqual(qg["fallos"], [])
        self.assertEqual(set(qg["criterios"]),
                         set(QUALITY_CRITERIA))

    def test_sin_cara_rechazado_duro(self):
        review = _review(
            decision="rechazado", pasa=False, hard_pasa=False,
            face={"pasa": False, "method": None, "share": 0.0,
                  "motivo": "sin cara verificada en el vídeo final"},
            fallos=["webcam_visible"])
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "rechazado")
        self.assertFalse(qg["pasa"])
        self.assertIn("webcam", qg["fallos"])
        self.assertIn("webcam", qg["fallos_duros"])

    def test_formato_mal_rechazado_duro(self):
        review = _review(
            decision="rechazado", pasa=False, hard_pasa=False,
            template={"pasa": False,
                      "checks": {
                          "dimensiones": {"pasa": False},
                          "titulo_banner": {"pasa": True},
                      }})
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "rechazado")
        self.assertIn("formato_9_16", qg["fallos"])
        self.assertIn("formato_9_16", qg["fallos_duros"])

    def test_sin_miniatura_rechazado_duro(self):
        qg = build_quality_gate(_review(), thumbnail_text="",
                                no_speech=False)
        self.assertEqual(qg["decision"], "rechazado")
        self.assertIn("miniatura", qg["fallos"])
        self.assertIn("miniatura", qg["fallos_duros"])

    def test_sin_audio_rechazado_duro(self):
        qg = build_quality_gate(_review(), thumbnail_text="EL GOL",
                                no_speech=True)
        self.assertEqual(qg["decision"], "rechazado")
        self.assertIn("audio", qg["fallos"])
        review = _review(transcript_words=0)
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertIn("audio", qg["fallos"])

    def test_hook_flojo_en_observacion_no_bloquea(self):
        scores = dict(ALL_SCORES, momento_comprensible=3.0)
        review = _review(decision="observado", scores=scores,
                         fallos=["momento_comprensible"])
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "observado")
        self.assertTrue(qg["pasa"])
        self.assertIn("hook", qg["fallos"])
        self.assertNotIn("hook", qg["fallos_duros"])

    def test_hook_flojo_sin_observacion_bloquea(self):
        scores = dict(ALL_SCORES, momento_comprensible=3.0)
        review = _review(decision="rechazado", pasa=False,
                         observe_only=False, scores=scores,
                         fallos=["momento_comprensible"])
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "rechazado")
        self.assertFalse(qg["pasa"])
        self.assertIn("hook", qg["fallos"])

    def test_titulo_flojo_en_observacion_no_bloquea(self):
        scores = dict(ALL_SCORES, titulo_legible=2.0)
        review = _review(decision="observado", scores=scores,
                         fallos=["titulo_legible"])
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "observado")
        self.assertTrue(qg["pasa"])
        self.assertIn("titulo", qg["fallos"])

    def test_pendiente_no_pasa(self):
        review = _review(decision="pendiente_revision", pasa=False,
                         hard_pasa=None, face=None, template=None,
                         scores={}, motivo="sin Gemini")
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "pendiente_revision")
        self.assertFalse(qg["pasa"])

    # P0 (2026-11-27, fallo abierto del juez): un pendiente_revision
    # con las capas deterministas medidas y pasadas (hard_pasa True)
    # NO es un rechazo — es el juez (Gemini) no disponible. Abre
    # solo si miniatura y audio (medidos aquí, no por el juez)
    # también pasaron; si no, sigue bloqueando. La decisión se
    # MANTIENE como pendiente_revision (con pasa True): el clip
    # sigue contando como pendiente y sigue siendo revalidable.
    def test_pendiente_con_hard_pasa_abre(self):
        review = _review(decision="pendiente_revision", pasa=False,
                         hard_pasa=True, scores={},
                         motivo="Gemini no responde: 503")
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "pendiente_revision")
        self.assertTrue(qg["pasa"])
        self.assertTrue(qg["hard_pasa"])
        self.assertFalse(qg["juez_disponible"])
        self.assertIn("juez no disponible", qg["motivo"])
        self.assertIn("fallo abierto del juez", qg["motivo"])

    def test_pendiente_con_hard_pasa_pero_sin_miniatura_no_abre(self):
        review = _review(decision="pendiente_revision", pasa=False,
                         hard_pasa=True, scores={},
                         motivo="Gemini no responde: 503")
        qg = build_quality_gate(review, thumbnail_text="")
        self.assertEqual(qg["decision"], "pendiente_revision")
        self.assertFalse(qg["pasa"])
        self.assertTrue(qg["hard_pasa"])

    def test_pendiente_con_hard_pasa_pero_sin_audio_no_abre(self):
        review = _review(decision="pendiente_revision", pasa=False,
                         hard_pasa=True, scores={}, transcript_words=0,
                         motivo="Gemini no responde: 503")
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "pendiente_revision")
        self.assertFalse(qg["pasa"])

    def test_pendiente_con_hard_pasa_false_no_abre(self):
        review = _review(decision="pendiente_revision", pasa=False,
                         hard_pasa=False,
                         face={"pasa": False, "method": "facecrop",
                               "share": 0.0,
                               "motivo": "sin cara verificada"},
                         scores={}, motivo="Gemini no responde: 503")
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "pendiente_revision")
        self.assertFalse(qg["pasa"])
        self.assertFalse(qg["hard_pasa"])

    def test_desactivada_sin_veredicto(self):
        review = _review(decision="desactivada", pasa=None,
                         hard_pasa=None, face=None, template=None,
                         scores={})
        qg = build_quality_gate(review, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "desactivada")
        self.assertIsNone(qg["pasa"])

    def test_sin_veredicto_no_pasa_nunca(self):
        qg = build_quality_gate({}, thumbnail_text="EL GOL")
        self.assertEqual(qg["decision"], "pendiente_revision")
        self.assertFalse(qg["pasa"])
        qg = build_quality_gate(None, thumbnail_text="EL GOL")
        self.assertFalse(qg["pasa"])


class TestPublishMode(unittest.TestCase):
    def test_aprobado_y_auto_api(self):
        self.assertEqual(publish_mode_for_quality(
            {"decision": "aprobado", "pasa": True}, True), "api")

    def test_aprobado_sin_auto_draft(self):
        self.assertEqual(publish_mode_for_quality(
            {"decision": "aprobado", "pasa": True}, False), "draft")

    def test_rechazado_con_auto_draft(self):
        self.assertEqual(publish_mode_for_quality(
            {"decision": "rechazado", "pasa": False}, True), "draft")

    def test_pendiente_con_auto_draft(self):
        self.assertEqual(publish_mode_for_quality(
            {"decision": "pendiente_revision", "pasa": False},
            True), "draft")

    def test_pendiente_abierto_por_fallo_con_auto_api(self):
        # P0 (2026-11-27): el fail-open del juez llega al puente
        # como "api" cuando auto_publish está encendido (pasa True
        # manda); sin auto, sigue siendo draft.
        gate = {"decision": "pendiente_revision", "pasa": True,
                "hard_pasa": True, "juez_disponible": False,
                "motivo": "juez no disponible: Gemini no responde"}
        self.assertEqual(publish_mode_for_quality(gate, True), "api")
        self.assertEqual(publish_mode_for_quality(gate, False), "draft")

    def test_sin_veredicto_no_cambia_lo_legacy(self):
        self.assertEqual(publish_mode_for_quality(None, True), "api")
        self.assertEqual(publish_mode_for_quality(None, False),
                         "draft")


class TestDiscardTracker(unittest.TestCase):
    def _tracker(self, tmp):
        return QualityDiscardTracker(
            os.path.join(tmp, "quality_gate_daily.json"))

    def test_supera_30_avisa_una_vez_al_dia(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = self._tracker(tmp)
            seen = [tracker.record(
                {"decision": "rechazado", "pasa": False},
                date="2026-11-20") for _ in range(3)]
            self.assertEqual(seen, [None, None, None])
            seen = [tracker.record(
                {"decision": "aprobado", "pasa": True},
                date="2026-11-20") for _ in range(2)]
            # 3/5 = 60% > 30%: la alerta sale al 5º registro.
            self.assertIsNone(seen[0])
            self.assertIsNotNone(seen[1])
            self.assertIn("3/5", seen[1])
            self.assertIn("60%", seen[1])
            # El 6º ya no avisa (dedupe por día).
            self.assertIsNone(tracker.record(
                {"decision": "rechazado", "pasa": False},
                date="2026-11-20"))
            # Al día siguiente el contador resetea y puede avisar.
            fresh = self._tracker(tmp)
            self.assertIsNone(fresh.record(
                {"decision": "aprobado", "pasa": True},
                date="2026-11-21"))

    def test_bajo_30_no_avisa(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker = self._tracker(tmp)
            for _ in range(4):
                self.assertIsNone(tracker.record(
                    {"decision": "aprobado", "pasa": True},
                    date="2026-11-20"))
            self.assertIsNone(tracker.record(
                {"decision": "rechazado", "pasa": False},
                date="2026-11-20"))

    def test_muestra_menor_que_5_no_avisa(self):
        self.assertEqual(QUALITY_ALERT_MIN_SAMPLE, 5)
        self.assertEqual(QUALITY_DISCARD_ALERT_PCT, 30.0)
        with tempfile.TemporaryDirectory() as tmp:
            tracker = self._tracker(tmp)
            for _ in range(2):
                self.assertIsNone(tracker.record(
                    {"decision": "rechazado", "pasa": False},
                    date="2026-11-20"))
            self.assertIsNone(tracker.record(
                {"decision": "aprobado", "pasa": True},
                date="2026-11-20"))


class FakeBridge:
    """Puente falso: captura el payload de POST /api/clips."""

    def __init__(self):
        self.payloads = []

    def create_clip(self, payload):
        self.payloads.append(payload)
        return {"id": 99}

    def upload_video(self, clip_id, path):
        return {"ok": True}

    def preview_seen(self, clip_id):
        return {"ok": True}

    def tracking_step(self, *a, **k):
        return {"ok": True}

    def publish_clip(self, clip_id):
        self.published = getattr(self, "published", [])
        self.published.append(clip_id)
        return {"jobs": [{"id": 7, "platform": "youtube_shorts"}],
                "blocked": []}


class TestEnqueuePanelQualityGate(unittest.TestCase):
    def _session(self, tmp, bridge):
        return LiveCutterSession(
            "rubius", platform="twitch", stream_id="2026-11-20",
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            bridge=bridge, dry_run=False, vod_id="123")

    def _record(self, quality_gate):
        pkg_path = os.path.join(tempfile.gettempdir(), "pkg.json")
        with open(pkg_path, "w", encoding="utf-8") as f:
            json.dump({"priority": {"youtube_shorts": {"priority": 80}},
                       "description": "d", "tags": []}, f)
        return {
            "clip_id": "live:rubius:x:p100", "clip": __file__,
            "title": "Rubius", "start_sec": 100, "end_sec": 140,
            "peak_ts": 167.0, "category": "Grand Theft Auto VI",
            "category_source": "helix", "game": "GTA 6",
            "origin": "live", "viral_total": 80.0,
            "viral_auto": 80.0, "review": {},
            "streamer_hype": {"count": 0, "distinct": 0,
                              "phrases": []},
            "subtitles_complete": True, "publish_blocked": False,
            "seo": {"youtube_shorts": 94, "tiktok": 92,
                    "instagram_reels": 91, "facebook_reels": 90},
            "publish_status": {}, "package_path": pkg_path,
            "hype_score": 8.0, "burst_score": 8.0, "hype_ratio": 4.0,
            "thumbnail_text": "EL GOL",
            "quality_gate": quality_gate,
        }

    def test_payload_lleva_el_veredicto(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            qg = {"decision": "aprobado", "pasa": True, "fallos": [],
                  "motivo": ""}
            session._enqueue_panel(self._record(qg))
            payload = bridge.payloads[0]
            self.assertEqual(payload["viral"]["quality_gate"], qg)

    def test_sin_veredicto_no_cambia_el_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            bridge = FakeBridge()
            session = self._session(tmp, bridge)
            record = self._record(None)
            record.pop("quality_gate")
            session._enqueue_panel(record)
            self.assertNotIn("viral", bridge.payloads[0])


class TestEnqueuePublishQualityGate(unittest.TestCase):
    def _session(self, tmp, store, auto_publish, bridge=None):
        return LiveCutterSession(
            "rubius", platform="twitch", store=store,
            out_dir=os.path.join(tmp, "clips"), base_dir=tmp,
            dry_run=False, auto_publish=auto_publish,
            bridge=bridge)

    def _record(self, quality_gate):
        return {
            "clip_id": "live:rubius:x:p100", "clip": __file__,
            "viral_total": 80, "publish_blocked": False,
            "publish_status": {
                "youtube_shorts": {"status": "publicable",
                                   "reason": "ok"}},
            "quality_gate": quality_gate,
        }

    def test_rechazado_no_encola_ni_en_borrador(self):
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                session = self._session(tmp, store, True)
                created = session._enqueue_publish(self._record(
                    {"decision": "rechazado", "pasa": False,
                     "fallos": ["webcam"]}))
                self.assertEqual(created, [])
                self.assertEqual(store.counts().get("pending", 0), 0)
            finally:
                store.close()

    def test_pendiente_con_auto_no_crea_job_local(self):
        # P0 #8: con veredicto pendiente el clip queda en la Cola
        # del panel como borrador; ya no se crea ningún job local
        # (el publish loop --live está retirado).
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                session = self._session(tmp, store, True)
                created = session._enqueue_publish(self._record(
                    {"decision": "pendiente_revision", "pasa": False,
                     "fallos": []}))
                self.assertEqual(created, [])
                self.assertEqual(store.counts().get("pending", 0), 0)
            finally:
                store.close()

    def test_aprobado_con_auto_publica_por_el_puente(self):
        # P0 #8: aprobado + auto_publish -> PanelBridge.publish_clip
        # (único publicador: el Worker); ningún job local.
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                bridge = FakeBridge()
                session = self._session(tmp, store, True,
                                        bridge=bridge)
                created = session._enqueue_publish(self._record(
                    {"decision": "aprobado", "pasa": True,
                     "fallos": []}), panel_clip_id=123)
                self.assertEqual(bridge.published, [123])
                self.assertEqual(created,
                                 [("youtube_shorts", 7, "api")])
                self.assertEqual(store.counts().get("pending", 0), 0)
            finally:
                store.close()

    def test_aprobado_sin_puente_no_publica(self):
        # P0 #8: aprobado + auto_publish pero sin puente al panel
        # (o sin clip en el panel): no se publica por adapters
        # directos; el clip queda en la Cola para el auto-enqueue
        # del ciclo del Worker.
        from clipagent.jobs import StepJobs
        with tempfile.TemporaryDirectory() as tmp:
            store = StepJobs(os.path.join(tmp, "queue.db"))
            try:
                session = self._session(tmp, store, True)
                created = session._enqueue_publish(self._record(
                    {"decision": "aprobado", "pasa": True,
                     "fallos": []}), panel_clip_id=123)
                self.assertEqual(created, [])
                self.assertEqual(store.counts().get("pending", 0), 0)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
