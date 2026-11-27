"""Rescate de layout VOD (spec §2.2B, espejo del live).

_rerender_vod_clip dispara también con "plantilla" en hard y
motivo de layout: sin_webcam -> re-corte con layout_mode="game"
y validación contra rubius_sin_webcam (la plantilla definitiva
viaja en review2["plantilla"] para el PATCH §8); ambiguo ->
revisión humana.
"""
import os
import tempfile
import unittest
from unittest import mock

from clipagent.vod_fallback import _rerender_vod_clip


def _review(hard, motivo):
    return {"decision": "pendiente_revision", "pasa": False,
            "hard_failures": hard, "motivo": motivo,
            "rerender_reason": "puerta dura", "fallos": hard}


class TestRerenderVodLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vod-sin-webcam-")
        segs = os.path.join(self.tmp, "segments")
        os.makedirs(segs)
        open(os.path.join(segs, "seg_01_t0m00s.mp4"), "wb").close()
        self.clip = {"n": 1, "start": 0, "duration": 40}
        self.sub = {"srt": os.path.join(self.tmp, "x.srt"),
                    "transcript": None}

    def _run(self, review, decide):
        video = os.path.join(self.tmp, "final.mp4")
        with mock.patch(
                "clipagent.template_decision.decide_template_for_segment",
                return_value=decide) as m_decide, \
            mock.patch("clipagent.thumbnails.get_duration",
                       return_value=40.0), \
            mock.patch("clipagent.render.cut_clip_with_layout") \
                as m_cut, \
            mock.patch("clipagent.workers.subtitle_clip",
                       return_value={"srt": self.sub["srt"],
                                     "transcript": None}), \
            mock.patch("clipagent.workers.apply_sfx",
                       return_value=(video, {"events": {}})), \
            mock.patch(
                "clipagent.final_review.transcript_text_for_srt",
                return_value="hola mundo"), \
            mock.patch(
                "clipagent.final_review.validate_final_clip",
                return_value={"decision": "aprobado",
                              "pasa": True}) as m_val:
            out = _rerender_vod_clip(
                review, video, video, self.sub, self.tmp, self.clip,
                {}, "es", "título", plantilla="rubius")
        return out, m_decide, m_cut, m_val

    def test_sin_webcam_recorte_game_y_plantilla(self):
        review = _review(
            ["plantilla"],
            "plantilla: layout (layout duplicado: el juego aparece "
            "también en la zona de cámara)")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius_sin_webcam", None, "firme", False))
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "game")
        self.assertEqual(out[0].get("plantilla"), "rubius_sin_webcam")
        self.assertEqual(
            m_val.call_args.kwargs["plantilla"], "rubius_sin_webcam")
        self.assertFalse(out[0].get("needs_human_review"))

    def test_ambiguo_a_revision_humana_sin_recorte(self):
        review = _review(
            ["plantilla"],
            "plantilla: layout (layout: sin cara estable en la zona "
            "de cámara)")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius", None, "ambiguo", False))
        self.assertTrue(out[0].get("needs_human_review"))
        m_cut.assert_not_called()

    def test_fallo_plantilla_no_layout_no_dispara_decision(self):
        review = _review(
            ["plantilla"],
            "plantilla: titulo (el título no está en el banner)")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius_sin_webcam", None, "firme", False))
        m_decide.assert_not_called()
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "auto")

    def test_cara_sin_layout_fallo_decide_y_avatar_revalida(self):
        # §4.4 (espejo live): "cara" en hard sin fallo de layout
        # corre la decisión; con avatar, re-corta en "auto" y la
        # re-validación lleva avatar=True.
        review = _review(["cara"], "cara: sin cara estable")
        (out, m_decide, m_cut, m_val) = self._run(
            review, ("rubius", None, "firme", True))
        m_decide.assert_called_once()
        self.assertEqual(m_cut.call_args.kwargs["layout_mode"], "auto")
        self.assertTrue(m_val.call_args.kwargs["avatar"])
        self.assertTrue(out[0].get("avatar"))
        self.assertFalse(out[0].get("needs_human_review"))


if __name__ == "__main__":
    unittest.main()
