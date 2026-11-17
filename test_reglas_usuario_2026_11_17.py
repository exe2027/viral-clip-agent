#!/usr/bin/env python3
"""Reglas del usuario 2026-11-17 (test_reglas_usuario_2026_11_17).

El usuario, al revisar el clip de IlloJuan publicado en test (21,0 s,
sin subtítulos), corrigió dos cosas y pidió que quedaran en los .py,
no solo en docs:

1. Los clips NO pueden ser tan cortos: 21 s es "muy poco".
2. Los subtítulos NO pueden tapar la cara del influencer / webcam.

Reglas codificadas aquí (config.yaml `rules:`, clipagent/config.py
RULES_*): mínimo 36 s, objetivo 40 s, tope duro 60 s, prohibido
25-35 s (regla de oro: ningún clip de 30 s), pre-roll 20 s antes del
pico, subtítulos siempre en la zona de juego (MarginV mínimo 680),
nunca tapar minimapa ni HUD, nunca afirmar un juego no verificado y
nunca quemar subtítulos alucinados (manda la transcripción corregida
por el usuario).
"""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import validate
from clipagent.config import (
    DEFAULT_CONFIG,
    RULES_FORBIDDEN_DURATION_RANGE,
    RULES_HARD_CAP_S,
    RULES_MIN_DURATION_S,
    RULES_PRE_ROLL_S,
    RULES_TARGET_DURATION_S,
    clip_duration_ok,
    load_config,
    subtitle_min_margin_v,
)
from clipagent.precise_cut import (
    DEFAULT_FIXED_DUR,
    DEFAULT_FIXED_PRE,
    enforce_min_duration,
    refine_cut,
)
from clipagent.transcribe import (
    effective_subtitle_margin_v,
    load_user_corrected_transcript,
    subtitles_overlap_webcam,
)
from clipagent.workers import (
    CLIP_DURATION_S,
    CLIP_MAX_DURATION_S,
    CLIP_MIN_DURATION_S,
    CLIP_PRE_S,
)


def _words(starts):
    """Transcript mínimo: palabras con start/end en segundos."""
    return {"segments": [{"words": [
        {"word": f"w{i}", "start": float(s), "end": float(s) + 0.4}
        for i, s in enumerate(starts)]}]}


class TestReglasDuracion20261117(unittest.TestCase):
    def test_minimo_36_objetivo_40_tope_60(self):
        self.assertEqual(RULES_MIN_DURATION_S, 36)
        self.assertEqual(RULES_TARGET_DURATION_S, 40)
        self.assertEqual(RULES_HARD_CAP_S, 60)
        self.assertEqual(RULES_FORBIDDEN_DURATION_RANGE, (25, 35))
        self.assertEqual(RULES_PRE_ROLL_S, 20)
        cfg = load_config()
        clips = cfg["clips"]
        self.assertEqual(clips["min_duration_s"], 36)
        self.assertEqual(clips["target_duration_s"], 40)
        self.assertEqual(clips["max_duration_s"], 60)
        self.assertEqual(clips["pre_roll"], 20)

    def test_minimo_fuera_del_rango_prohibido(self):
        # 36 es el primer segundo entero ESTRICTAMENTE por encima de
        # 25-35: el 35 es el borde mismo y no vale como mínimo.
        self.assertGreater(RULES_MIN_DURATION_S, 35)
        self.assertNotIn(RULES_TARGET_DURATION_S, range(25, 36))

    def test_clip_duration_ok_rechaza_corto_prohibido_y_largo(self):
        ok, why = clip_duration_ok(21.0)
        self.assertFalse(ok)
        self.assertIn("36", why)
        ok, _ = clip_duration_ok(30.0)
        self.assertFalse(ok)
        ok, _ = clip_duration_ok(35.0)
        self.assertFalse(ok)
        ok, _ = clip_duration_ok(61.0)
        self.assertFalse(ok)
        for good in (36.0, 40.0, 60.0):
            ok, _ = clip_duration_ok(good)
            self.assertTrue(ok, f"{good} s debe pasar")

    def test_workers_usan_objetivo_40_y_rango_36_60(self):
        self.assertEqual(CLIP_PRE_S, 20)
        self.assertEqual(CLIP_DURATION_S, 40)
        self.assertEqual(CLIP_MIN_DURATION_S, 36)
        self.assertEqual(CLIP_MAX_DURATION_S, 60)

    def test_refine_cut_alarga_el_corte_corto_hasta_36(self):
        # Frase corta que antes dejaba un clip de ~7 s: ahora se
        # alarga hasta 36 s sin mover el inicio del momento.
        transcript = _words([18.5, 19.0, 19.6, 20.1, 20.7, 21.3])
        cut = refine_cut(60.0, 20.0, transcript=transcript)
        self.assertGreaterEqual(cut["duration"], 36.0)
        self.assertLessEqual(cut["duration"], 60.0)
        self.assertIn("36", cut["reason"])

    def test_refine_cut_fijo_pico_menos_20_y_minimo_36(self):
        # Sin transcripción fiable: caída pico -20 s / +40 s.
        cut = refine_cut(60.0, 20.0, transcript=None)
        self.assertEqual(cut["method"], "fixed")
        self.assertEqual(cut["start"], 0.0)
        self.assertEqual(cut["duration"], 40.0)
        self.assertEqual(DEFAULT_FIXED_PRE, 20)
        self.assertEqual(DEFAULT_FIXED_DUR, 40)

    def test_refine_cut_no_pasa_del_tope_60(self):
        transcript = _words([float(s) for s in range(0, 120, 2)])
        cut = refine_cut(120.0, 60.0, transcript=transcript)
        self.assertLessEqual(cut["duration"], 60.0)

    def test_enforce_min_duration_no_recorta_el_momento(self):
        start, end, _ = enforce_min_duration(16.0, 23.0, 60.0)
        self.assertEqual(start, 16.0)
        self.assertEqual(end - start, 36.0)
        # Si el segmento se acaba, retrocede el inicio en vez de
        # dejar el clip corto.
        start, end, _ = enforce_min_duration(50.0, 57.0, 60.0)
        self.assertEqual(end - start, 36.0)
        self.assertEqual(end, 60.0)

    def test_enforce_min_duration_aplica_el_tope(self):
        # Bug real 2026-11-17: la ruta fija (40 s) con --max-duration
        # 36 salía de 40 s porque el tope no se aplicaba. Ahora el
        # tope recorta hasta 36 s.
        start, end, note = enforce_min_duration(
            0.0, 40.0, 60.0, min_duration=36, max_duration=36)
        self.assertEqual((start, end), (0.0, 36.0))
        self.assertIsNone(note)
        cut = refine_cut(60.0, 20.0, transcript=None,
                         max_duration=36, min_duration=36)
        self.assertEqual(cut["duration"], 36.0)
        self.assertEqual(cut["start"], 0.0)

    def test_validate_rechaza_min_21_y_objetivo_30(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["clips"]["min_duration_s"] = 21
        cfg["clips"]["target_duration_s"] = 30
        cfg["clips"]["max_duration_s"] = 40
        with self.assertRaises(validate.ConfigError) as ctx:
            validate.validate_config(cfg)
        msg = str(ctx.exception)
        self.assertIn("min_duration_s", msg)
        self.assertIn("target_duration_s", msg)
        # La config real del repo pasa.
        self.assertEqual(validate.validate_config(load_config()), [])


class TestReglasSubtitulos20261117(unittest.TestCase):
    def test_margin_v_minimo_680_en_config_y_estilos(self):
        self.assertEqual(subtitle_min_margin_v(), 680)
        self.assertEqual(subtitle_min_margin_v(load_config()), 680)
        for name, style in load_config()["subtitle_styles"].items():
            self.assertGreaterEqual(style["margin_v"], 680, name)

    def test_margin_v_viejo_140_tapaba_la_webcam(self):
        # Con MarginV 140 el borde inferior del texto queda en
        # y=1780, dentro de la cámara (1320-1920): encima de la cara.
        self.assertTrue(subtitles_overlap_webcam(140))
        self.assertTrue(subtitles_overlap_webcam(140, fontsize=48))
        # Con 680 queda en y=1240, dentro del juego, 80 px por encima.
        self.assertFalse(subtitles_overlap_webcam(680))
        self.assertFalse(subtitles_overlap_webcam(680, fontsize=64))

    def test_burn_corrige_el_margin_v_viejo_solo(self):
        self.assertEqual(effective_subtitle_margin_v(140), 680)
        self.assertEqual(effective_subtitle_margin_v(140, load_config()), 680)
        self.assertEqual(effective_subtitle_margin_v(700), 700)

    def test_transcripcion_corregida_manda_sobre_whisper(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "clips"))
            corrected = {
                "text": "nooo noooo no cometas esa locura,",
                "corrected_by": "usuario",
                "segments": [{"start_v3_clip_s": 18.54,
                              "end_v3_clip_s": 21.82,
                              "text": "nooo noooo no cometas esa locura,"}],
                "superseded_hallucinations_do_not_use": [
                    "Bueno, no me da el saloncóra"],
            }
            path = os.path.join(
                tmp, "clips", "clip_x.transcript_corrected.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(corrected, f, ensure_ascii=False)
            got = load_user_corrected_transcript(tmp, "clip_x")
            self.assertIsNotNone(got)
            self.assertEqual(got["text"],
                             "nooo noooo no cometas esa locura,")
            self.assertNotIn("saloncóra", json.dumps(got))
            self.assertIsNone(load_user_corrected_transcript(tmp, "clip_y"))


if __name__ == "__main__":
    unittest.main()
