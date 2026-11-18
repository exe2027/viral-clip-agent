#!/usr/bin/env python3
"""Pruebas de 7.2: el audio como señal del score (picos de energía,
peso configurable y mayor en Kick).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.audio import shout_spikes, streamer_shout_spikes
from clipagent.detect import (audio_component, audio_weight_for,
                              score_clip)

DETAIL = {"msgs": 50, "risas": 12, "hype": 5, "unique_users": 30}


def _audio(std, steady, range_db, words):
    return {"stats": {"std_db": std, "steady_frac": steady,
                      "range_db": range_db, "max_dbfs": -5.0,
                      "min_dbfs": -5.0 - range_db},
            "words": words}


class TestAudioComponent(unittest.TestCase):
    def test_plano_sigue_bajo(self):
        # Plano: 2,0; con pocas palabras no hay bonus de palabras.
        value, _ = audio_component(_audio(5.0, 0.6, 8.0, 10))
        self.assertEqual(value, 2.0)
        # Con 60 palabras el bonus existente (+1) sigue aplicando.
        value, _ = audio_component(_audio(5.0, 0.6, 8.0, 60))
        self.assertEqual(value, 3.0)

    def test_picos_de_energia_suman(self):
        base, _ = audio_component(_audio(12.0, 0.2, 10.0, 60))
        peak, reason = audio_component(_audio(12.0, 0.2, 30.0, 60))
        self.assertGreater(peak, base)
        self.assertIn("picos de energía", reason)

    def test_rango_medio_suma_medio_punto(self):
        low, _ = audio_component(_audio(12.0, 0.2, 10.0, 60))
        mid, _ = audio_component(_audio(12.0, 0.2, 20.0, 60))
        self.assertAlmostEqual(mid - low, 0.5)

    def test_sin_voz_sigue_con_tope(self):
        value, _ = audio_component(_audio(16.0, 0.2, 30.0, 0))
        self.assertLessEqual(value, 4.0)

    def test_sin_audio_es_pendiente(self):
        self.assertEqual(audio_component(None)[0], None)


class TestAudioWeight(unittest.TestCase):
    def test_pesos_por_defecto(self):
        self.assertEqual(audio_weight_for("twitch"), 1.0)
        self.assertEqual(audio_weight_for("kick"), 1.5)
        self.assertEqual(audio_weight_for("twitch", {}), 1.0)

    def test_pesos_desde_config(self):
        cfg = {"scoring": {"audio_weight": 0.5,
                           "audio_weight_kick": 2.0}}
        self.assertEqual(audio_weight_for("twitch", cfg), 0.5)
        self.assertEqual(audio_weight_for("kick", cfg), 2.0)

    def test_total_sin_cambio_con_peso_por_defecto(self):
        # Con el peso por defecto el total es idéntico al de siempre
        # (renormalización x10/10 = 1).
        audio = _audio(16.0, 0.2, 30.0, 60)
        a = score_clip(DETAIL, 8.0, rank=1, human=None, audio=audio)
        b = score_clip(DETAIL, 8.0, rank=1, human=None, audio=audio,
                       audio_weight=1.0)
        self.assertEqual(a["total"], b["total"])
        self.assertEqual(a["audio_weight"], 1.0)

    def test_kick_pesa_mas_que_twitch_con_audio_fuerte(self):
        audio = _audio(16.0, 0.2, 30.0, 60)
        tw = score_clip(DETAIL, 8.0, rank=1, human=None, audio=audio,
                        platform="twitch")
        ki = score_clip(DETAIL, 8.0, rank=1, human=None, audio=audio,
                        platform="kick")
        self.assertGreater(ki["total"], tw["total"])
        self.assertEqual(ki["audio_weight"], 1.5)
        self.assertLessEqual(ki["total"], 100.0)

    def test_total_siempre_en_0_100(self):
        audio = _audio(20.0, 0.1, 40.0, 200)
        for w in (0.0, 0.5, 1.0, 2.0, 5.0):
            total = score_clip(DETAIL, 12.0, rank=1, human=None,
                               audio=audio, audio_weight=w)["total"]
            self.assertGreaterEqual(total, 0.0)
            self.assertLessEqual(total, 100.0)


class TestShoutSpikes(unittest.TestCase):
    """Gritos por decibeles (2026-11-18): salto brusco sobre la
    mediana de los 10 s anteriores, no nivel alto constante."""

    def _rms(self, levels):
        return [(s, float(db)) for s, db in enumerate(levels)]

    def test_grito_detectado(self):
        levels = [-32.0] * 20 + [-16.0, -15.0] + [-32.0] * 18
        self.assertEqual(shout_spikes(self._rms(levels)), [20, 21])

    def test_nivel_alto_constante_no_es_grito(self):
        self.assertEqual(shout_spikes(self._rms([-16.0] * 40)), [])

    def test_subida_gradual_no_es_grito(self):
        levels = [-35.0 + 0.75 * s for s in range(20)]
        self.assertEqual(shout_spikes(self._rms(levels)), [])

    def test_susurro_que_sube_no_es_grito(self):
        # Salto de 15 dB pero por debajo de -30 dBFS: no audible.
        levels = [-60.0] * 20 + [-45.0, -44.0] + [-60.0] * 18
        self.assertEqual(shout_spikes(self._rms(levels)), [])

    def test_sin_baseline_no_puntua(self):
        levels = [-16.0, -15.0, -32.0, -32.0]
        self.assertEqual(shout_spikes(self._rms(levels)), [])


class TestStreamerShoutSpikes(unittest.TestCase):
    """Gritos SOLO del streamer (2026-11-18): el salto de decibeles
    cuenta solo si Whisper dice que el streamer hablaba ahí. Una
    explosión del juego con el streamer callado no cuenta."""

    def test_solo_cuenta_si_habla(self):
        levels = [-32.0] * 40
        levels[20] = -16.0
        levels[21] = -15.0
        levels[35] = -15.0  # explosión del juego, sin voz
        rms = [(s, float(db)) for s, db in enumerate(levels)]
        transcript = {"segments": [{"start": 19.0, "end": 23.0,
                                     "text": "no lo puedo creer"}]}
        self.assertEqual(streamer_shout_spikes(rms, transcript),
                         [20, 21])

    def test_sin_transcripcion_no_hay_gritos_del_streamer(self):
        levels = [-32.0] * 20 + [-16.0, -15.0] + [-32.0] * 18
        rms = [(s, float(db)) for s, db in enumerate(levels)]
        self.assertEqual(streamer_shout_spikes(rms, None), [])
        self.assertEqual(streamer_shout_spikes(rms, {}), [])

    def test_sin_voz_no_hay_gritos_del_streamer(self):
        levels = [-32.0] * 20 + [-16.0, -15.0] + [-32.0] * 18
        rms = [(s, float(db)) for s, db in enumerate(levels)]
        transcript = {"segments": []}
        self.assertEqual(streamer_shout_spikes(rms, transcript), [])

    def test_solo_palabras_con_timestamp_tambien_valen(self):
        levels = [-32.0] * 20 + [-16.0, -15.0] + [-32.0] * 18
        rms = [(s, float(db)) for s, db in enumerate(levels)]
        transcript = {"segments": [
            {"text": "no creo",
             "words": [{"word": "no", "start": 19.0, "end": 19.4},
                       {"word": "creo", "start": 22.0,
                        "end": 22.5}]}]}
        # speech_segments cae a palabras: tramo 19,0-22,5.
        self.assertEqual(streamer_shout_spikes(rms, transcript),
                         [20, 21])


if __name__ == "__main__":
    unittest.main(verbosity=2)
