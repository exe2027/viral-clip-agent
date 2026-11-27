"""Rescate de layout + plantilla en viral (spec §2.2B y §8).

- _rerender_after_gate dispara también con "plantilla" en hard
  y motivo de layout (duplicación / cámara contaminada):
  sin_webcam -> re-render con layout_mode="game" y validación
  contra rubius_sin_webcam; ambiguo -> revisión humana;
  rubius con caja -> re-render rubius con la caja forzada.
- _enqueue_panel manda record["plantilla"] dentro de viral.
"""
import os
import tempfile
import unittest
from unittest import mock

from clipagent.live_cutter import LiveCutterSession


def _session(tmp):
    return LiveCutterSession(
        "rubius", out_dir=tmp, cfg={}, dry_run=True,
        bridge=mock.Mock())


def _final_review(hard, motivo):
    return {"decision": "pendiente_revision", "pasa": False,
            "hard_failures": hard, "motivo": motivo,
            "rerender_reason": "puerta dura", "fallos": hard}


class TestRerenderAfterGateLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sin-webcam-")
        self.s = _session(self.tmp)
        self.s._report_event = mock.Mock()

    def _run(self, review, decide):
        sub = {"srt": os.path.join(self.tmp, "x.srt"),
               "transcript": None}
        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=decide) as m_decide, \
            mock.patch("clipagent.workers.cut_clip") as m_cut, \
            mock.patch("clipagent.workers.subtitle_clip",
                       return_value={"srt": sub["srt"],
                                     "transcript": None}), \
            mock.patch("clipagent.workers.apply_sfx",
                       return_value=(os.path.join(self.tmp, "r1.mp4"),
                                     {"events": {}})), \
            mock.patch(
                "clipagent.final_review.transcript_text_for_srt",
                return_value="hola mundo"), \
            mock.patch(
                "clipagent.final_review.validate_final_clip",
                return_value={"decision": "aprobado",
                              "pasa": True}) as m_val:
            out = self.s._rerender_after_gate(
                review, os.path.join(self.tmp, "final.mp4"), sub,
                self.tmp, os.path.join(self.tmp, "ventana.mp4"),
                0.0, "título", 1, hype_s=[20.0], plantilla="rubius")
        return out, m_decide, m_cut, m_val

    def test_sin_webcam_rerender_game_y_plantilla(self):
        review = _final_review(
            ["plantilla"],
            "plantilla: layout (layout duplicado: el juego aparece "
            "también en la zona de cámara)")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius_sin_webcam", None, "firme", False))
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "game")
        self.assertEqual(
            m_val.call_args.kwargs["plantilla"], "rubius_sin_webcam")
        self.assertFalse(out[0].get("needs_human_review"))

    def test_ambiguo_a_revision_humana_sin_recorte(self):
        review = _final_review(
            ["plantilla"],
            "plantilla: layout (layout: sin cara estable en la zona "
            "de cámara)")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius", None, "ambiguo", False))
        self.assertTrue(out[0].get("needs_human_review"))
        m_cut.assert_not_called()

    def test_rubius_con_caja_rerender_rubius_forzado(self):
        review = _final_review(
            ["plantilla"],
            "plantilla: layout (cámara contaminada: la cara de "
            "primer plano está desplazada)")
        box = {"x": 10, "y": 5, "w": 60, "h": 40}
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius", box, "firme", False))
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "auto")
        self.assertEqual(
            m_cut.call_args.kwargs["cfg"]["facecam_box"], box)
        self.assertEqual(m_val.call_args.kwargs["plantilla"], "rubius")

    def test_fallo_plantilla_no_layout_no_dispara_decision(self):
        review = _final_review(
            ["plantilla"],
            "plantilla: titulo (el título no está en el banner)")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius_sin_webcam", None, "firme", False))
        m_decide.assert_not_called()
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "auto")

    def test_cara_sin_layout_fallo_decide_y_avatar_revalida(self):
        # §4.4: "cara" en hard SIN fallo de layout ya no va directo
        # a revisión humana: corre la decisión §4.3; con avatar,
        # re-corta en "auto" y la re-validación lleva avatar=True.
        review = _final_review(["cara"], "cara: sin cara estable")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius", None, "firme", True))
        m_decide.assert_called_once()
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "auto")
        self.assertTrue(m_val.call_args.kwargs["avatar"])
        self.assertTrue(out[0].get("avatar"))
        self.assertFalse(out[0].get("needs_human_review"))


class TestEnqueuePanelPlantilla(unittest.TestCase):
    def test_viral_lleva_plantilla(self):
        tmp = tempfile.mkdtemp(prefix="sin-webcam-")
        bridge = mock.Mock()
        bridge.create_clip.return_value = {"id": 99}
        s = LiveCutterSession("rubius", out_dir=tmp, cfg={},
                              dry_run=True, bridge=bridge)
        record = {
            "title": "t", "start_sec": 0, "end_sec": 40,
            "viral_total": 55.0, "clip": os.path.join(tmp, "no.mp4"),
            "seo": {}, "thumbnail_text": None,
            "subtitles_complete": True, "publish_blocked": False,
            "review": {}, "viral": {}, "quality_gate": None,
            "needs_human_review": False,
            "final_review": {"decision": "aprobado"},
            "plantilla": "rubius_sin_webcam",
            "package_path": os.path.join(tmp, "package.json"),
            "category": "GTA VI", "category_source": "helix",
            "peak_ts": 1000.0,
        }
        s._enqueue_panel(record)
        payload = bridge.create_clip.call_args.args[0]
        self.assertEqual(payload["viral"]["plantilla"],
                         "rubius_sin_webcam")

    def test_sin_plantilla_no_hay_clave(self):
        tmp = tempfile.mkdtemp(prefix="sin-webcam-")
        bridge = mock.Mock()
        bridge.create_clip.return_value = {"id": 99}
        s = LiveCutterSession("rubius", out_dir=tmp, cfg={},
                              dry_run=True, bridge=bridge)
        record = {
            "title": "t", "start_sec": 0, "end_sec": 40,
            "viral_total": 55.0, "clip": os.path.join(tmp, "no.mp4"),
            "seo": {}, "thumbnail_text": None,
            "subtitles_complete": True, "publish_blocked": False,
            "review": {}, "viral": {}, "quality_gate": None,
            "needs_human_review": False,
            "final_review": {"decision": "aprobado"},
            "plantilla": None,
            "package_path": os.path.join(tmp, "package.json"),
            "category": "GTA VI", "category_source": "helix",
            "peak_ts": 1000.0,
        }
        s._enqueue_panel(record)
        payload = bridge.create_clip.call_args.args[0]
        self.assertNotIn("plantilla", payload.get("viral") or {})


if __name__ == "__main__":
    unittest.main()
