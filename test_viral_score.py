#!/usr/bin/env python3
"""Pruebas del score de viralidad 0-100 + señales MVP (viral_score.py).

Los 3 fixtures son sintéticos, con los números de la revisión
interna (casos reales pasados, solo como referencia offline para
probar la fórmula; NUNCA como fuentes activas ni clips a encolar):
  * tipo Coker: 45 msgs, 5 risas, 9 hype, 47 únicos, múltiplo 8,5x
    -> ~78 prioritario.
  * tipo Locones: 0 risas, 0 hype, múltiplo alto por volumen
    -> ~29 descartar (el tope de 0 risas/hype manda).
  * tipo peereira7: chat alto pero claridad/novedad medias
    -> ~62 revisar.
Los componentes humanos 1,2,3,6,7 se pasan como entrada en el test.

Uso: python3 test_viral_score.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from viral_score import (  # noqa: E402
    audio_component,
    band,
    chat_component,
    compute_signals,
    filter_passed,
    score_clip,
)

# Fixture tipo Coker: 45 msgs, 5 risas, 9 hype, 47 únicos, 8,5x.
COKER_DETAIL = {"msgs": 45, "risas": 5, "hype": 9, "users": 47,
                "emote_ratio": 0.1, "rep_ratio": 0.2, "lex_diversity": 0.6,
                "penalty_factor": 1.0, "motivo_penalizacion": None}
COKER_HUMAN = {"hook": 8, "claridad": 9, "novedad": 7, "loop": 8, "packaging": 7}
COKER_AUDIO = {"std_db": 11.5, "steady_frac": 0.2, "words": 77}

# Fixture tipo Locones: volumen alto, 0 risas, 0 hype, audio plano.
LOCONES_DETAIL = {"msgs": 60, "risas": 0, "hype": 0, "users": 40,
                  "emote_ratio": 0.1, "rep_ratio": 0.2, "lex_diversity": 0.5,
                  "penalty_factor": 1.0, "motivo_penalizacion": None}
LOCONES_HUMAN = {"hook": 3, "claridad": 4, "novedad": 3, "loop": 2, "packaging": 3}
LOCONES_AUDIO = {"std_db": 7.4, "steady_frac": 0.58, "words": 0}

# Fixture tipo peereira7: chat alto, claridad/novedad medias.
PEEREIRA_DETAIL = {"msgs": 120, "risas": 8, "hype": 10, "users": 60,
                   "emote_ratio": 0.15, "rep_ratio": 0.25, "lex_diversity": 0.5,
                   "penalty_factor": 1.0, "motivo_penalizacion": None}
PEEREIRA_HUMAN = {"hook": 7, "claridad": 5, "novedad": 5, "loop": 6, "packaging": 6}
PEEREIRA_AUDIO = {"std_db": 10.0, "steady_frac": 0.3, "words": 120}


class TestBand(unittest.TestCase):
    def test_band_boundaries(self):
        self.assertEqual(band(0), "descartar")
        self.assertEqual(band(49.9), "descartar")
        self.assertEqual(band(50), "revisar")
        self.assertEqual(band(69.9), "revisar")
        self.assertEqual(band(70), "prioritario")
        self.assertEqual(band(84.9), "prioritario")
        self.assertEqual(band(85), "urgente")
        self.assertEqual(band(100), "urgente")


class TestChatComponent(unittest.TestCase):
    def test_bands(self):
        d = {"msgs": 100, "risas": 20, "hype": 10, "users": 80}
        self.assertAlmostEqual(chat_component(d, 1.0)[0], 1.0)
        self.assertAlmostEqual(chat_component(d, 3.0)[0], 3.5)
        self.assertAlmostEqual(chat_component(d, 5.5)[0], 5.5)
        self.assertAlmostEqual(chat_component(d, 8.5)[0], 7.5)
        self.assertAlmostEqual(chat_component(d, 12.0)[0], 9.2)

    def test_cap_no_laugh_no_hype(self):
        # Múltiplo 12x, pero 0 risas y 0 hype -> máx. 3/10.
        d = {"msgs": 60, "risas": 0, "hype": 0, "users": 40}
        value, _reasons, topes = chat_component(d, 12.0)
        self.assertEqual(value, 3.0)
        self.assertTrue(any("0 risas" in t for t in topes))

    def test_cap_low_signal_ratio(self):
        # (risas+hype)/msgs = 5% < 10% -> máx. 4/10.
        d = {"msgs": 100, "risas": 3, "hype": 2, "users": 80}
        value, _r, topes = chat_component(d, 8.0)
        self.assertEqual(value, 4.0)
        self.assertTrue(any("10%" in t for t in topes))

    def test_cap_low_unique_users(self):
        # usuarios únicos/msgs = 0,2 < 0,4 -> máx. 5/10.
        d = {"msgs": 100, "risas": 20, "hype": 10, "users": 20}
        value, _r, topes = chat_component(d, 8.0)
        self.assertEqual(value, 5.0)
        self.assertTrue(any("0,4" in t for t in topes))

    def test_no_msgs(self):
        value, _r, topes = chat_component({"msgs": 0}, 5.0)
        self.assertEqual(value, 0.0)


class TestAudioComponent(unittest.TestCase):
    def test_flat_audio(self):
        value, reason = audio_component({"std_db": 7.4, "steady_frac": 0.58,
                                         "words": 0})
        self.assertEqual(value, 2.0)
        self.assertIn("plano", reason)

    def test_dynamic_audio(self):
        value, _r = audio_component({"std_db": 11.5, "steady_frac": 0.2,
                                     "words": 77})
        self.assertGreaterEqual(value, 7.0)

    def test_no_voice_caps(self):
        # std alto pero 0 palabras -> máx. 4/10.
        value, _r = audio_component({"std_db": 16.0, "steady_frac": 0.1,
                                     "words": 0})
        self.assertEqual(value, 4.0)

    def test_no_audio_is_none(self):
        value, _r = audio_component(None)
        self.assertIsNone(value)


class TestFilter(unittest.TestCase):
    def test_passes(self):
        ok, _m = filter_passed(COKER_DETAIL, 8.5, rank=1)
        self.assertTrue(ok)

    def test_low_multiple_fails(self):
        ok, motivos = filter_passed(COKER_DETAIL, 2.5, rank=1)
        self.assertFalse(ok)
        self.assertTrue(any("3x" in m for m in motivos))

    def test_rank_over_5_fails(self):
        ok, motivos = filter_passed(COKER_DETAIL, 8.5, rank=6)
        self.assertFalse(ok)
        self.assertTrue(any("top-5" in m for m in motivos))

    def test_emote_spam_only_fails(self):
        d = {"msgs": 50, "risas": 0, "hype": 0, "users": 40,
             "emote_ratio": 0.8, "rep_ratio": 0.1}
        ok, motivos = filter_passed(d, 8.0, rank=1)
        self.assertFalse(ok)
        self.assertTrue(any("emote-spam" in m for m in motivos))


class TestScoreClipFixtures(unittest.TestCase):
    def test_coker_prioritario(self):
        r = score_clip(COKER_DETAIL, 8.5, rank=1,
                       human=COKER_HUMAN, audio=COKER_AUDIO)
        # Fórmula: 8*2 + 9*1,5 + 7*1,5 + 7,5*1,5 + 8,12*1 + 8*1,5 + 7*1.
        self.assertAlmostEqual(r["total"], 78.4, delta=0.2)
        self.assertEqual(r["band"], "prioritario")
        self.assertFalse(r["beta"])
        self.assertTrue(r["filter_passed"])
        self.assertEqual(r["chat_multiple"], 8.5)
        self.assertEqual(r["components"]["chat"]["value"], 7.5)
        self.assertFalse(r["components"]["chat"]["estimated"])

    def test_locones_descartar(self):
        r = score_clip(LOCONES_DETAIL, 12.0, rank=1,
                       human=LOCONES_HUMAN, audio=LOCONES_AUDIO)
        # Chat capado a 3 (0 risas/hype) pese al 12x; audio plano 2.
        # 3*2 + 4*1,5 + 3*1,5 + 3*1,5 + 2*1 + 2*1,5 + 3*1 = 29,0.
        self.assertAlmostEqual(r["total"], 29.0, delta=0.2)
        self.assertEqual(r["band"], "descartar")
        self.assertEqual(r["components"]["chat"]["value"], 3.0)
        self.assertEqual(r["components"]["audio"]["value"], 2.0)

    def test_peereira_revisar(self):
        r = score_clip(PEEREIRA_DETAIL, 9.0, rank=2,
                       human=PEEREIRA_HUMAN, audio=PEEREIRA_AUDIO)
        # Chat 7,67 (9x); audio 6,5; humanos medios -> ~62 revisar.
        self.assertAlmostEqual(r["total"], 62.0, delta=0.5)
        self.assertEqual(r["band"], "revisar")
        self.assertTrue(r["filter_passed"])

    def test_beta_without_human_input(self):
        # Sin entrada humana: componentes en None/pendiente, total BETA
        # con estimación conservadora 5,0, nunca un 7-8 inventado.
        r = score_clip(COKER_DETAIL, 8.5, rank=1, human=None, audio=None)
        self.assertTrue(r["beta"])
        self.assertIsNotNone(r["beta_note"])
        for name in ("hook", "claridad", "novedad", "loop", "packaging",
                     "audio"):
            self.assertTrue(r["components"][name]["estimated"])
            self.assertEqual(r["components"][name]["value"], 5.0)
        self.assertFalse(r["components"]["chat"]["estimated"])
        # Total con estimados: 5*2+5*1,5+5*1,5+7,5*1,5+5*1+5*1,5+5*1 = 53,75.
        self.assertAlmostEqual(r["total"], 53.8, delta=0.2)
        self.assertEqual(r["band"], "revisar")

    def test_partial_human_input(self):
        r = score_clip(COKER_DETAIL, 8.5, rank=1,
                       human={"hook": 9}, audio=COKER_AUDIO)
        self.assertTrue(r["beta"])
        self.assertEqual(r["components"]["hook"]["value"], 9.0)
        self.assertFalse(r["components"]["hook"]["estimated"])
        self.assertTrue(r["components"]["claridad"]["estimated"])

    def test_all_components_present(self):
        r = score_clip(COKER_DETAIL, 8.5, rank=1,
                       human=COKER_HUMAN, audio=COKER_AUDIO)
        self.assertEqual(set(r["components"]),
                         {"hook", "claridad", "novedad", "chat", "audio",
                          "loop", "packaging"})


class TestSignals(unittest.TestCase):
    def test_from_detail_only(self):
        s = compute_signals(detail=COKER_DETAIL, peak_ts=100, clip_start=80)
        self.assertEqual(s["unique_users_per_10s"], 47)
        self.assertAlmostEqual(s["laugh_hype_ratio"], 14 / 45, places=3)
        self.assertEqual(s["payoff_position_s"], 20.0)
        # Sin audio, transcripción ni vídeo: todo lo demás es None.
        self.assertIsNone(s["rms_peak_delta_db"])
        self.assertIsNone(s["silence_before_s"])
        self.assertIsNone(s["exclamations_per_min"])
        self.assertIsNone(s["faces_1fps"])
        self.assertIsNone(s["peak_coincidence_s"])
        self.assertIsNone(s["hook_words_0_3s"])

    def test_rms_and_transcript(self):
        # RMS sintético: plano -30 dBFS, pico -10 dBFS en el s 20.
        rms = [(s, -30.0) for s in range(60)]
        rms[20] = (20, -10.0)
        transcript = {"segments": [
            {"text": "¡Qué jugada! ¿Lo viste?", "start": 0.0, "end": 4.0,
             "words": [{"word": "¡Qué", "start": 0.5, "end": 0.8},
                       {"word": "jugada!", "start": 0.9, "end": 1.4},
                       {"word": "¿Lo", "start": 2.0, "end": 2.3},
                       {"word": "viste?", "start": 2.4, "end": 2.9}]},
            {"text": "No puede ser qué locura", "start": 19.0, "end": 21.0,
             "words": [{"word": "No", "start": 19.5, "end": 19.8},
                       {"word": "puede", "start": 19.9, "end": 20.1},
                       {"word": "ser", "start": 20.2, "end": 20.4},
                       {"word": "qué", "start": 20.5, "end": 20.7},
                       {"word": "locura", "start": 20.8, "end": 21.0}]},
        ]}
        s = compute_signals(detail=COKER_DETAIL, audio_rms=rms,
                            transcript=transcript, peak_ts=100, clip_start=80)
        self.assertAlmostEqual(s["rms_peak_delta_db"], 20.0, places=1)
        # 4 signos (!, ?, ¡, ¿) en 21 s -> ~11,4/min.
        self.assertAlmostEqual(s["exclamations_per_min"], 4 / (21 / 60), delta=0.5)
        self.assertEqual(s["hook_words_0_3s"], 4)
        coinc = s["peak_coincidence_s"]
        self.assertEqual(coinc["chat_peak"], 20.0)
        self.assertEqual(coinc["audio_peak"], 20.0)
        self.assertTrue(coinc["coinciden"])

    def test_no_video_no_faces(self):
        s = compute_signals(detail=COKER_DETAIL)
        self.assertIsNone(s["faces_1fps"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
