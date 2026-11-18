#!/usr/bin/env python3
"""Regresión (auditoría security-audit run-1, LOW
live.LiveSession.unbounded-chat-accumulation): la retención del
chat en vivo y el trabajo por tick están acotados, no crecen con
el total de mensajes alimentados.

- LivePeakDetector retiene como mucho 20.000 mensajes y solo la
  ventana de los últimos 900 s.
- El tiempo de tick no crece linealmente con el total alimentado:
  tras 100k y tras 200k mensajes el tick puntúa la misma ventana
  retenida, así que su duración es del mismo orden.
- LiveSession.run no se bloquea con una ráfaga que desborda la
  cola acotada (la bomba descarta lo más viejo y sigue).
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.live import (LIVE_CHAT_MAX_MESSAGES,
                            LIVE_CHAT_WINDOW_S, LivePeakDetector,
                            LiveSession)


def _feed(detector, t0, start, count, per_sec=100):
    """count mensajes sintéticos a per_sec msgs/s desde start."""
    for i in range(start, start + count):
        detector.add_message(f"u{i % 500}", f"qué buen directo {i}",
                             t0 + i / per_sec)


class TestLiveBoundedRetention(unittest.TestCase):
    def test_200k_mensajes_retencion_acotada_y_tick_plano(self):
        detector = LivePeakDetector(t0=1000.0)
        # 100k mensajes en 1000 s (100 msgs/s): la ventana de
        # 900 s ya recorta; el tope de 20.000 recorta más.
        _feed(detector, 1000.0, 0, 100_000)
        self.assertLessEqual(len(detector.messages),
                             LIVE_CHAT_MAX_MESSAGES)
        retained = [m[2] for m in detector.messages]
        self.assertLessEqual(max(retained) - min(retained),
                             LIVE_CHAT_WINDOW_S + 1)
        t_start = time.perf_counter()
        detector.tick()
        tick_100k = time.perf_counter() - t_start

        # Otros 100k mensajes (200k en total, 2000 s de chat):
        # la retención y el tick no deben crecer con el total.
        _feed(detector, 1000.0, 100_000, 100_000)
        self.assertLessEqual(len(detector.messages),
                             LIVE_CHAT_MAX_MESSAGES)
        self.assertEqual(len(detector.messages),
                         LIVE_CHAT_MAX_MESSAGES)
        retained = [m[2] for m in detector.messages]
        self.assertLessEqual(max(retained) - min(retained),
                             LIVE_CHAT_WINDOW_S + 1)
        t_start = time.perf_counter()
        detector.tick()
        tick_200k = time.perf_counter() - t_start

        # Mismo trabajo por tick (misma ventana retenida): el
        # segundo tick no puede ser varias veces el primero.
        # Margen amplio (x3) para no ser frágil en CI lento.
        self.assertLessEqual(tick_200k, max(3 * tick_100k, 1.0))
        self.assertLess(tick_200k, 30.0)

    def test_ventana_deslizante_descarta_lo_viejo(self):
        detector = LivePeakDetector(t0=1000.0)
        _feed(detector, 1000.0, 0, 100, per_sec=1)
        # 100 mensajes en 100 s: nada que recortar todavía.
        self.assertEqual(len(detector.messages), 100)
        # Un mensaje 2000 s después: solo queda la ventana
        # de los últimos 900 s (este mensaje).
        detector.add_message("u1", "hola", 1000.0 + 2000)
        self.assertEqual(len(detector.messages), 1)
        self.assertAlmostEqual(detector.messages[0][2], 2000)

    def test_sesion_no_se_bloquea_con_rafaga_mayor_que_la_cola(self):
        with tempfile.TemporaryDirectory() as tmp:
            session = LiveSession("illojuan", base_dir=tmp,
                                  notify_fn=lambda e, d: None)
            t0 = time.time()
            # 5000 mensajes instantáneos (5x la cola de 1000)
            # en 5 s de chat: la bomba debe soltarlos sin
            # bloquearse y la sesión terminar en su duración.
            messages = [("u%d" % (i % 50), "hola %d" % i,
                         t0 + i * 0.001) for i in range(5000)]
            t_start = time.perf_counter()
            peaks = session.run(iter(messages), duration_s=2,
                                tick_s=3600)
            elapsed = time.perf_counter() - t_start
            self.assertIsInstance(peaks, list)
            self.assertLess(elapsed, 20.0)
            self.assertLessEqual(len(session.detector.messages),
                                 LIVE_CHAT_MAX_MESSAGES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
