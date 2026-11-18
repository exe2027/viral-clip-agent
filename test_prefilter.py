#!/usr/bin/env python3
"""Pre-filtro rápido de Óscar Boix (2026-11-18).

- Un pico con muchas reacciones pasa (3x la mediana, conteo 1/2).
- Un tramo plano no pasa (sin picos del pre-filtro).
- Separación de 90 s: dos tramos fuertes pegados dan un solo pico.
- Salvaguarda de chat caliente: si ningún tramo llega a 3x, el
  pre-filtro se desactiva solo y NO descarta nada (medido en Rubius
  2932415021: máx. 2,23x; 3220 con viralidad 53,8 y 7775 con 52,5
  tienen 1,19x y 1,87x y no se pueden perder).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.prefilter import (
    count_windows,
    find_prefilter_peaks,
    prefilter_gate,
    reaction_weight,
)

REAL_CHAT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "runs", "2932415021", "chat_full.json")


def _msgs(spec):
    """spec: [(offset, texto, nº_usuarios)] -> [(offset, texto)]."""
    out = []
    for offset, body, n in spec:
        for j in range(n):
            out.append((offset, body))
    return out


def _cold_chat_with_burst(burst_ts=300, burst_msgs=60):
    """Chat frío 10 min (2 msgs/10 s) + ráfaga en burst_ts.

    La ráfaga: mitad 'jajaja' (reacción, 2 pts) y mitad 'qué locura'
    (1 pto; no es reacción en el regex de Óscar)."""
    msgs = []
    for t in range(0, 600, 5):
        msgs.append((t, "hola"))
    for j in range(burst_msgs):
        body = "jajaja" if j % 2 == 0 else "qué locura"
        msgs.append((burst_ts + j % 10, body))
    return msgs


class TestReactionWeight(unittest.TestCase):
    def test_reaccion_vale_doble(self):
        for text in ("jajaja", "JAJAJA", "xd", "xdd", "kekw", "lul",
                     "omegalul", "lmao", "clip it", "noooo", "wtf",
                     "POG", "😂", "🤣", "💀", "jajajaja"):
            self.assertEqual(reaction_weight(text), 2, text)

    def test_mensaje_normal_vale_uno(self):
        for text in ("hola", "buenas", "qué jugada", "vamos rubius",
                     "cinema", "goty", ""):
            self.assertEqual(reaction_weight(text), 1, text)


class TestCountWindows(unittest.TestCase):
    def test_tramos_vacios_cuentan_cero(self):
        points = count_windows([(0, "hola"), (100, "jajaja")])
        self.assertEqual(len(points), 11)
        self.assertEqual(points[0], 1)
        self.assertEqual(points[10], 2)
        self.assertEqual(points[5], 0)


class TestFindPrefilterPeaks(unittest.TestCase):
    def test_pico_con_muchas_reacciones_pasa(self):
        msgs = _cold_chat_with_burst(burst_ts=300, burst_msgs=60)
        points = count_windows(msgs)
        peaks = find_prefilter_peaks(points)
        self.assertEqual([p["peak_sec"] for p in peaks], [300])
        self.assertGreaterEqual(peaks[0]["multiple"], 3.0)

    def test_tramo_plano_no_pasa(self):
        msgs = [(t, "hola") for t in range(0, 600, 5)]
        peaks = find_prefilter_peaks(count_windows(msgs))
        self.assertEqual(peaks, [])

    def test_separacion_90s_evita_duplicados(self):
        msgs = _cold_chat_with_burst(burst_ts=300, burst_msgs=60)
        msgs += _cold_chat_with_burst(burst_ts=310, burst_msgs=60)[120:]
        msgs += _cold_chat_with_burst(burst_ts=410, burst_msgs=60)[120:]
        peaks = find_prefilter_peaks(count_windows(msgs))
        secs = [p["peak_sec"] for p in peaks]
        # 300 y 310 están a 10 s: solo uno; 410 está a 100 s de 310.
        self.assertEqual(len(secs), 2)
        self.assertIn(410, secs)
        self.assertTrue(secs[0] in (300, 310))


class TestPrefilterGate(unittest.TestCase):
    def test_pico_fuerte_pasa_y_debil_se_descarta(self):
        msgs = _cold_chat_with_burst(burst_ts=300, burst_msgs=60)
        pf = find_prefilter_peaks(count_windows(msgs))
        passed, discarded, summary = prefilter_gate(
            [(300, 50.0), (500, 40.0)], pf)
        self.assertEqual(passed, [(300, 50.0)])
        self.assertEqual([d["peak"] for d in discarded], [500])
        self.assertIn("pre-filtro", discarded[0]["motivos"][0])
        self.assertTrue(summary["active"])

    def test_sin_picos_prefiltro_no_se_descarta_nada(self):
        # Salvaguarda de chat caliente: pre-filtro vacío -> pasa todo.
        passed, discarded, summary = prefilter_gate(
            [(3220, 14.3), (7775, 15.5)], [])
        self.assertEqual(passed, [(3220, 14.3), (7775, 15.5)])
        self.assertEqual(discarded, [])
        self.assertFalse(summary["active"])

    def test_tolerancia_un_tramo_vecino(self):
        # La rejilla v2.5 es de 5 s y el pre-filtro de 10 s: un pico
        # a 305 s (tramo 300) pasa aunque el tramo del pre-filtro
        # sea el 300.
        msgs = _cold_chat_with_burst(burst_ts=300, burst_msgs=60)
        pf = find_prefilter_peaks(count_windows(msgs))
        passed, discarded, _ = prefilter_gate([(305, 50.0)], pf)
        self.assertEqual(passed, [(305, 50.0)])
        self.assertEqual(discarded, [])


class TestGateWithPipelineSeparation(unittest.TestCase):
    """Regresión E2E (test_auto_clips_filter): picos reales a 40/90/140 s.

    Con la separación de Óscar (90 s) el pico de 90 s se perdía (a
    50 s del de 40 s) y el E2E sacaba 2 clips en vez de 3. La puerta
    usa separation = min(--min-separation, config): con
    --min-separation 20 los tres pasan.
    """
    def _e2e_msgs(self):
        msgs = [(t, "hola") for t in range(0, 200, 5)]
        for peak in (40, 90, 140):
            for j in range(25):
                msgs.append((peak + j % 10, "jajaja qué risa"))
        return msgs

    def test_los_tres_picos_reales_pasan_con_separacion_20(self):
        points = count_windows(self._e2e_msgs())
        pf = find_prefilter_peaks(points, separation=20)
        self.assertEqual([p["peak_sec"] for p in pf], [40, 90, 140])
        passed, discarded, summary = prefilter_gate(
            [(40, 7.27), (90, 7.27), (140, 7.27)], pf)
        self.assertEqual(passed, [(40, 7.27), (90, 7.27), (140, 7.27)])
        self.assertEqual(discarded, [])
        self.assertTrue(summary["active"])

    def test_separacion_90_si_perderia_el_de_90s(self):
        # Documenta la trampa: el default de Óscar (90 s) NO sirve
        # como separación de la puerta cuando el pipeline admite
        # picos a 50 s.
        points = count_windows(self._e2e_msgs())
        pf = find_prefilter_peaks(points, separation=90)
        self.assertEqual([p["peak_sec"] for p in pf], [40, 140])
@unittest.skipUnless(os.path.isfile(REAL_CHAT),
                     "sin chat_full.json de Rubius 2932415021")
class TestRealVodRubius(unittest.TestCase):
    def test_rubius_no_pierde_sus_mejores_clips(self):
        # Medido 2026-11-18: ningún tramo llega a 3x (máx. 2,23x).
        # El pre-filtro se desactiva solo y 3220 (viralidad 53,8)
        # y 7775 (52,5) pasan a la puntuación completa.
        from clipagent.prefilter import prefilter_peaks_from_chat
        pf = prefilter_peaks_from_chat(REAL_CHAT)
        self.assertEqual(pf, [])
        peaks = [(3220, 14.3), (7775, 15.54), (7150, 5.89), (7615, 17.04)]
        passed, discarded, summary = prefilter_gate(peaks, pf)
        self.assertEqual(passed, peaks)
        self.assertEqual(discarded, [])
        self.assertFalse(summary["active"])


class TestWiring(unittest.TestCase):
    def test_auto_clips_importa_prefilter(self):
        import auto_clips
        self.assertTrue(auto_clips.prefilter_mod)
        self.assertTrue(hasattr(auto_clips, "prefilter_gate"))


if __name__ == "__main__":
    unittest.main()
