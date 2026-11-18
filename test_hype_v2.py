#!/usr/bin/env python3
"""HYPE v2 (2026-11-18): el hype se mide por VELOCIDAD, no por total.

Regla del usuario 2026-11-18 (mejorada el mismo día): una ráfaga
repentina de comentarios pesa más que un chat alto pero estable.
El algoritmo:

- Velocidad en ventanas cortas: msgs/10 s, msgs/60 s (rodante) y
  msgs/s, no solo el total acumulado.
- Baseline rodante: MEDIANA de los msgs/10 s de los 5 min previos
  (sin contar la ventana misma, para que la ráfaga no se infle su
  propio baseline). Lo que manda es el RATIO pico/baseline.
- Picos de velocidad: el score de detección suma +0,2 × hype_score.
- Señales dentro de la ráfaga: risas, emotes de hype, repetición
  del mismo texto por usuarios DISTINTOS (un canto del chat) y
  msgs/s.
- hype_score 0-10 queda guardado por pico (valor, ventana,
  baseline, ratio) y alimenta el componente chat de viralidad
  (detect.py, bonus +0,25 × hype_score antes de los topes
  anti-spam). Los pesos de la fórmula de prioridad NO cambian
  (viralidad^0,5 × SEO^0,3 × chat_norm^0,2).

Prueba clave: una ráfaga corta y fuerte (60 msgs en 10 s) supera
a un momento con MÁS mensajes totales (120) pero repartidos en
4 minutos.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.detect import chat_component
from clipagent.ingest import score_windows
from clipagent.priority import DEFAULT_WEIGHTS
from clipagent.workers import detect_peaks


def _chat_file(tmp):
    """VOD de 15 min: chat bajo y estable (2 msgs/10 s), una ráfaga
    de 60 msgs en 10 s (t=300) y 120 msgs repartidos en 4 min
    (t=600-840, más mensajes totales que la ráfaga)."""
    comments = []

    def msg(offset, body, user):
        comments.append({
            "content_offset_seconds": int(offset),
            "message": {"body": body},
            "commenter": {"name": user},
        })

    # Baseline: 1 msg cada 5 s, texto plano, usuarios distintos.
    for i, t in enumerate(range(0, 900, 5)):
        msg(t, "buenas", f"base{i}")
    # Ráfaga t=300-309: 60 msgs en 10 s, 60 usuarios distintos:
    # 30x "67" (canto), 15x "jajaja" (risa), 15x "vamos rubius".
    burst_bodies = (["67"] * 30 + ["jajaja"] * 15
                    + ["vamos rubius"] * 15)
    for i, body in enumerate(burst_bodies):
        msg(300 + (i % 10), body, f"burst{i}")
    # Repartido t=600-839: 120 msgs (el doble que la ráfaga),
    # 1 cada 2 s, texto variado (sin canto repetido), usuarios
    # distintos.
    for i, t in enumerate(range(600, 840, 2)):
        msg(t, f"que bien juega el numero {i}", f"spread{i}")
    path = os.path.join(tmp, "chat.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"comments": comments}, f, ensure_ascii=False)
    return path


class TestHypeV2(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.chat = _chat_file(self.tmp.name)
        self.scores, self.detail = score_windows(self.chat)

    def tearDown(self):
        self.tmp.cleanup()

    def test_velocidad_en_ventanas_cortas(self):
        d = self.detail[300]
        # 60 de la ráfaga + 2 del chat bajo (t=300 y t=305).
        self.assertEqual(d["msgs"], 62)
        self.assertEqual(d["msgs_per_10s"], 62)
        self.assertEqual(d["msgs_per_sec"], 6.2)
        self.assertGreaterEqual(d["msgs_per_60s"], 60)
        # La ventana repartida: 5 repartidos + 2 del chat bajo.
        self.assertEqual(self.detail[660]["msgs"], 7)
        self.assertEqual(self.detail[660]["msgs_per_10s"], 7)

    def test_baseline_rodante_mediana_no_la_infla_la_rafaga(self):
        d = self.detail[300]
        # Baseline ~2 msgs/10 s (el chat bajo previo); la ráfaga de
        # 60 no entra en su propio baseline.
        self.assertAlmostEqual(d["hype_baseline"], 2.0, places=1)
        self.assertGreaterEqual(d["hype_ratio"], 10.0)
        self.assertEqual(d["hype_window_s"], 10)

    def test_rafaga_corta_supera_a_mas_mensajes_repartidos(self):
        burst = self.detail[300]
        spread = self.detail[660]
        # Más mensajes totales en el momento repartido (120 vs 60),
        # pero menos hype: la velocidad manda.
        self.assertGreater(burst["hype_score"], spread["hype_score"])
        self.assertGreaterEqual(burst["hype_score"], 7.5)
        self.assertLess(spread["hype_score"], 5.0)
        # Y el pico de detección es la ráfaga, no el momento largo.
        top = max(self.scores, key=lambda s: self.scores[s])
        self.assertEqual(top, 300)

    def test_senales_dentro_de_la_rafaga(self):
        comp = self.detail[300]["hype_components"]
        self.assertEqual(comp["velocidad"], 5.0)  # ratio >= 4x
        self.assertGreater(comp["risas"], 0.0)    # 15 jajaja
        self.assertGreater(comp["repeticion"], 0.0)  # canto "67"
        self.assertAlmostEqual(
            comp["velocidad"] + comp["risas"] + comp["emotes_hype"]
            + comp["repeticion"], self.detail[300]["hype_score"],
            places=1)

    def test_hype_guardado_por_pico(self):
        result = detect_peaks(self.chat)
        peaks = result["peaks"]
        self.assertTrue(peaks)
        top = peaks[0]
        self.assertEqual(top["start"], 300)
        for key in ("hype_score", "hype_window_s", "hype_baseline",
                    "hype_ratio", "hype_velocity_z", "hype_components",
                    "msgs_per_60s", "msgs_per_sec"):
            self.assertIn(key, top, key)
        self.assertGreaterEqual(top["hype_score"], 7.5)
        self.assertGreaterEqual(top["hype_ratio"], 10.0)

    def test_hype_alimenta_viralidad_sin_romper_topes(self):
        # Mismo múltiplo y mismos mensajes: con hype sube, sin hype no.
        base = {"msgs": 100, "risas": 20, "hype": 10, "users": 80}
        sin, _r, _t = chat_component(dict(base), 5.5)
        self.assertAlmostEqual(sin, 5.5)
        con, _r, _t = chat_component(
            dict(base, hype_score=8.0, hype_ratio=4.0,
                 hype_baseline=2.0), 5.5)
        self.assertAlmostEqual(con, 7.5)  # 5,5 + 0,25×8
        # Sin risas ni hype el tope de 3/10 sigue mandando aunque
        # haya velocidad: la velocidad sola no maquilla el chat.
        vacio = {"msgs": 60, "risas": 0, "hype": 0, "users": 40,
                 "hype_score": 9.0, "hype_ratio": 8.0,
                 "hype_baseline": 2.0}
        value, _r, topes = chat_component(vacio, 12.0)
        self.assertAlmostEqual(value, 3.0)
        self.assertTrue(any("3" in t for t in topes))
        # Nunca pasa de 10.
        top = {"msgs": 100, "risas": 50, "hype": 50, "users": 90,
               "hype_score": 10.0, "hype_ratio": 9.0,
               "hype_baseline": 2.0}
        self.assertLessEqual(chat_component(top, 12.0)[0], 10.0)

    def test_pesos_de_prioridad_intocables(self):
        self.assertEqual(DEFAULT_WEIGHTS,
                         {"viral": 0.5, "seo": 0.3, "chat": 0.2})


if __name__ == "__main__":
    unittest.main()
