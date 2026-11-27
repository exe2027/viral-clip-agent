"""Decisión de plantilla por segmento (spec rubius sin webcam, §2.1/§10).

Una sola función de decisión, compartida por el pre-chequeo (§2.2A) y
el rescate (§2.2B). La tabla normativa (§2.1):

- caja presente (voto >= 2 o rescate) -> ("rubius", caja)
- caja ausente, hits == 1 -> ("rubius", None, "ambiguo") — nunca
  sin_webcam sobre una señal débil (M5)
- caja ausente, hits == 0, sin avatar -> ("rubius_sin_webcam", None)
- avatar reactivo -> ("rubius", None), nunca sin_webcam

Los tests sintéticos corren siempre (CI); la detección real se
inyecta (detect_fn/avatar_fn) para no depender de vídeos.
"""
import os
import tempfile
import unittest
from unittest import mock

from clipagent.template_decision import (
    AMBIGUO,
    RUBIUS,
    SIN_WEBCAM,
    decide_template_for_segment,
)


def _detect(box=None, hits=0, method="none"):
    return {"facecam_box": box, "method": method,
            "samples": 5, "hits": hits, "boxes": []}


class TestDecideTemplate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".mp4",
                                                delete=False)
        self._tmp.close()
        self.segment = self._tmp.name

    def tearDown(self):
        os.unlink(self.segment)

    def _decide(self, detect=None, avatar=None, **kw):
        return decide_template_for_segment(
            self.segment, 30.0, cfg={},
            detect_fn=detect, avatar_fn=avatar, **kw)

    def test_voto_mayoria_rubius_con_caja(self):
        with mock.patch(
                "clipagent.template_decision._a_px_fuente",
                return_value=[100, 100, 400, 200]):
            got = self._decide(
                detect=lambda *a, **k: _detect([10, 10, 40, 20],
                                               hits=3,
                                               method="detected"))
        self.assertEqual(got, (RUBIUS, [100, 100, 400, 200]))

    def test_rescate_interno_hits_1_con_caja_es_rubius(self):
        # Un rescate con éxito devuelve hits == 1 CON caja: es facecam,
        # no ambiguo (B10: la decisión es sobre (caja, hits) juntos).
        with mock.patch(
                "clipagent.template_decision._a_px_fuente",
                return_value=[50, 60, 300, 150]):
            got = self._decide(
                detect=lambda *a, **k: _detect([8, 9, 30, 15],
                                               hits=1, method="edge"))
        self.assertEqual(got, (RUBIUS, [50, 60, 300, 150]))

    def test_sin_caja_sin_hits_sin_avatar_es_sin_webcam(self):
        got = self._decide(
            detect=lambda *a, **k: _detect(None, hits=0),
            avatar=lambda: False)
        self.assertEqual(got, (SIN_WEBCAM, None))

    def test_hits_1_sin_caja_es_ambiguo_nunca_sin_webcam(self):
        # Paso 3 antes que el 4: aunque hubiera avatar, un solo voto
        # sin rescate es ambiguo, no sin_webcam y no rubius decidido.
        got = self._decide(
            detect=lambda *a, **k: _detect(None, hits=1),
            avatar=lambda: True)
        self.assertEqual(got, (RUBIUS, None, AMBIGUO))

    def test_avatar_es_rubius_nunca_sin_webcam(self):
        got = self._decide(
            detect=lambda *a, **k: _detect(None, hits=0),
            avatar=lambda: True)
        self.assertEqual(got, (RUBIUS, None))

    def test_caja_de_config_no_se_reescala(self):
        # method "config": la caja ya viene en px de la fuente.
        got = self._decide(
            detect=lambda *a, **k: _detect([100, 50, 400, 200],
                                           hits=0, method="config"))
        self.assertEqual(got, (RUBIUS, [100, 50, 400, 200]))

    def test_rescate_externo_yunet(self):
        with mock.patch("clipagent.yunet_facecam.yunet_facecam_box",
                        return_value=[200, 100, 300, 150]):
            got = self._decide(
                detect=lambda *a, **k: _detect(None, hits=0))
        self.assertEqual(got, (RUBIUS, [200, 100, 300, 150]))

    def test_rescate_externo_bordes_solo_conf_high(self):
        with mock.patch("clipagent.yunet_facecam.yunet_facecam_box",
                        return_value=None), mock.patch(
                            "clipagent.edge_facecam.detect",
                            return_value={"conf": "high", "x": 10,
                                        "y": 20, "w": 300, "h": 150}):
            got = self._decide(
                detect=lambda *a, **k: _detect(None, hits=0))
        self.assertEqual(got, (RUBIUS, [10, 20, 300, 150]))

    def test_bordes_conf_baja_no_rescata(self):
        with mock.patch("clipagent.yunet_facecam.yunet_facecam_box",
                        return_value=None), mock.patch(
                            "clipagent.edge_facecam.detect",
                            return_value={"conf": "low", "x": 10,
                                        "y": 20, "w": 300, "h": 150}):
            got = self._decide(
                detect=lambda *a, **k: _detect(None, hits=0),
                avatar=lambda: False)
        self.assertEqual(got, (SIN_WEBCAM, None))

    def test_segmento_inexistente_es_ambiguo(self):
        got = decide_template_for_segment(
            "/no/existe/segmento.mp4", 30.0, cfg={},
            detect_fn=lambda *a, **k: _detect(None, hits=0),
            avatar_fn=lambda: False)
        self.assertEqual(got, (RUBIUS, None, AMBIGUO))

    def test_deteccion_que_levanta_es_ambiguo(self):
        def _boom(*a, **k):
            raise RuntimeError("sin detector")

        got = self._decide(detect=_boom, avatar=lambda: False)
        self.assertEqual(got, (RUBIUS, None, AMBIGUO))

    def test_conversion_muestra_a_fuente(self):
        from clipagent.template_decision import _a_px_fuente

        with mock.patch("clipagent.render._probe_size",
                        return_value=(1920, 1080)):
            self.assertEqual(
                _a_px_fuente([16, 9, 80, 45], self.segment, {}),
                [192, 108, 960, 540])


if __name__ == "__main__":
    unittest.main()
