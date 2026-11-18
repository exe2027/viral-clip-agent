"""Salida limpia del corte en vivo cuando el directo termina.

Caso real 2026-11-18 (corrida cerebro-live 44382779254): xqc dejó
de estar en vivo entre el detect y el corte; el gate devolvió
categoría '' y `run_live` elevaba RuntimeError -> el job marcaba
failure aunque no había nada que cortar. Un directo terminado (o
cambiado de juego) es una salida normal: exit 0, sin picos. Solo
un error de lectura de la API ("sin lectura ...") sigue siendo un
fallo real.
"""

import tempfile
import unittest
from unittest import mock

from clipagent.live_cutter import CategoryGate, LiveCutterSession


class _GateStub:
    def __init__(self, result, mode="live"):
        self._result = result
        self.mode = mode
        self.log = []

    def check(self, ts=None, chat_path=None):
        return self._result


class TestRunLiveGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _session(self, gate):
        return LiveCutterSession(
            "rubius", platform="kick", out_dir=self.tmp.name,
            gate=gate, dry_run=True, cfg={})

    def test_directo_terminado_sale_limpia_sin_picos(self):
        gate = _GateStub((False, "", "kick api/v2/channels"))
        session = self._session(gate)
        peaks = session.run_live(duration_s=60)
        self.assertEqual(peaks, [])

    def test_cambio_de_juego_sale_limpia_sin_picos(self):
        gate = _GateStub((False, "Just Chatting", "helix"))
        session = self._session(gate)
        peaks = session.run_live(duration_s=60)
        self.assertEqual(peaks, [])

    def test_sin_lectura_sigue_siendo_fallo(self):
        gate = _GateStub((False, "", "sin lectura (timeout)"))
        session = self._session(gate)
        with self.assertRaises(RuntimeError):
            session.run_live(duration_s=60)

    def test_kick_sin_lectura_devuelve_motivo(self):
        with mock.patch(
                "clipagent.live.kick_channel_category",
                side_effect=RuntimeError("timeout")):
            gate = CategoryGate(platform="kick", streamer="xqc")
            cat, source = gate._kick_live_category()
        self.assertEqual(cat, "")
        self.assertTrue(source.startswith("sin lectura"),
                        source)

    def test_kick_sin_directo_categoria_vacia(self):
        with mock.patch(
                "clipagent.live.kick_channel_category",
                return_value=""):
            gate = CategoryGate(platform="kick", streamer="xqc")
            cat, source = gate._kick_live_category()
        self.assertEqual(cat, "")
        self.assertEqual(source, "kick api/v2/channels")


if __name__ == "__main__":
    unittest.main()
