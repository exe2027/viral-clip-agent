#!/usr/bin/env python3
"""Reglas del usuario 2026-11-17 (test_reglas_usuario_2026_11_17).

El usuario, al revisar el clip de IlloJuan publicado en test (21,0 s,
sin subtítulos), corrigió dos cosas y pidió que quedaran en los .py,
no solo en docs:

1. Los clips NO pueden ser tan cortos: 21 s es "muy poco".
2. Los subtítulos NO pueden tapar la cara del influencer / webcam.

Reglas codificadas aquí (config.yaml `rules:`, clipagent/config.py
RULES_*): SIN mínimo ni rango prohibido desde 2026-11-21 08:47
(el usuario eliminó la regla 40-60 / 25-35 "para siempre, en todo
el canal"): mínimo 1 s, objetivo 40 s, tope duro 60 s, pre-roll
20 s antes del pico, subtítulos siempre en la zona de juego
(MarginV 230 ref, mínimo 200), nunca tapar minimapa ni HUD,
nunca afirmar un juego no verificado y nunca quemar subtítulos
alucinados (manda la transcripción corregida por el usuario).
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
    CLIP_PRE_S,
)


def _words(starts):
    """Transcript mínimo: palabras con start/end en segundos."""
    return {"segments": [{"words": [
        {"word": f"w{i}", "start": float(s), "end": float(s) + 0.4}
        for i, s in enumerate(starts)]}]}


class TestReglasDuracion20261117(unittest.TestCase):
    def test_sin_minimo_objetivo_40_tope_60(self):
        # Regla 40-60 / 25-35 ELIMINADA por el usuario 2026-11-21
        # 08:47 ("para siempre, en todo el canal", P0 #6): sin
        # mínimo (1 s) ni rango prohibido; solo el tope duro 60 s.
        self.assertEqual(RULES_MIN_DURATION_S, 1)
        self.assertEqual(RULES_TARGET_DURATION_S, 40)
        self.assertEqual(RULES_HARD_CAP_S, 60)
        self.assertEqual(RULES_FORBIDDEN_DURATION_RANGE, ())
        self.assertEqual(RULES_PRE_ROLL_S, 20)
        cfg = load_config()
        clips = cfg["clips"]
        self.assertEqual(clips["min_duration_s"], 1)
        self.assertEqual(clips["target_duration_s"], 40)
        self.assertEqual(clips["max_duration_s"], 60)
        self.assertEqual(clips["pre_roll"], 20)

    def test_sin_rango_prohibido_los_cortos_pasan(self):
        # Sin rango prohibido: 21, 30, 35 y 38 s ya no frenan.
        self.assertEqual(RULES_FORBIDDEN_DURATION_RANGE, ())
        for dur in (21.0, 30.0, 35.0, 38.0):
            ok, _ = clip_duration_ok(dur)
            self.assertTrue(ok, f"{dur} s debe pasar")

    def test_clip_duration_ok_solo_rechaza_mas_de_60(self):
        for good in (21.0, 30.0, 35.0, 38.0, 40.0, 50.0, 60.0):
            ok, _ = clip_duration_ok(good)
            self.assertTrue(ok, f"{good} s debe pasar")
        ok, why = clip_duration_ok(61.0)
        self.assertFalse(ok)
        self.assertIn("60", why)

    def test_workers_usan_objetivo_40_y_solo_tope_60(self):
        self.assertEqual(CLIP_PRE_S, 20)
        self.assertEqual(CLIP_DURATION_S, 40)
        self.assertEqual(CLIP_MAX_DURATION_S, 60)
        # P0 #6: CLIP_MIN_DURATION_S ya no existe (sin mínimo).
        import clipagent.workers as _w
        self.assertFalse(hasattr(_w, "CLIP_MIN_DURATION_S"))

    def test_refine_cut_no_alarga_el_corte_corto(self):
        # Frase corta que antes se alargaba hasta 40 s: ahora el
        # corte queda corto (sin mínimo) y solo se respeta el tope.
        transcript = _words([18.5, 19.0, 19.6, 20.1, 20.7, 21.3])
        cut = refine_cut(60.0, 20.0, transcript=transcript)
        self.assertLess(cut["duration"], 40.0)
        self.assertLessEqual(cut["duration"], 60.0)

    def test_refine_cut_fijo_pico_menos_20_y_minimo_40(self):
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
        # Sin mínimo (P0 #6): un corte de 7 s ya no se alarga
        # hasta 40 s; se queda como está (sin recortar el momento).
        start, end, _ = enforce_min_duration(16.0, 23.0, 60.0)
        self.assertEqual(start, 16.0)
        self.assertEqual(end - start, 7.0)
        start, end, _ = enforce_min_duration(50.0, 57.0, 60.0)
        self.assertEqual(end - start, 7.0)
        self.assertEqual(end, 57.0)

    def test_enforce_min_duration_aplica_el_tope(self):
        # Bug real 2026-11-17: la ruta fija (40 s) con --max-duration
        # 36 salía de 40 s porque el tope no se aplicaba. Ahora el
        # tope recorta hasta el tope (aquí 40 s = min = max).
        start, end, note = enforce_min_duration(
            0.0, 40.0, 60.0, min_duration=40, max_duration=40)
        self.assertEqual((start, end), (0.0, 40.0))
        self.assertIsNone(note)
        cut = refine_cut(60.0, 20.0, transcript=None,
                         max_duration=40, min_duration=40)
        self.assertEqual(cut["duration"], 40.0)
        self.assertEqual(cut["start"], 0.0)

    def test_validate_acepta_min_21_y_objetivo_30(self):
        # Regla 40-60 / 25-35 ELIMINADA (P0 #6): min 21 y objetivo
        # 30 ya no se rechazan (21 <= 30 <= 40, tope 60). Solo
        # sigue frenando un max por encima de 60.
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["clips"]["min_duration_s"] = 21
        cfg["clips"]["target_duration_s"] = 30
        cfg["clips"]["max_duration_s"] = 40
        self.assertEqual(validate.validate_config(cfg), [])
        cfg["clips"]["max_duration_s"] = 61
        with self.assertRaises(validate.ConfigError) as ctx:
            validate.validate_config(cfg)
        self.assertIn("max_duration_s", str(ctx.exception))
        # La config real del repo pasa.
        self.assertEqual(validate.validate_config(load_config()), [])


class TestReglasSubtitulos20261117(unittest.TestCase):
    def test_margin_v_minimo_200_en_config_y_estilos(self):
        # Geometría RDJavi v2 (2026-11-18): los subtítulos viven
        # en la barra negra 1740-1920 (MarginV 12); el mínimo es 0
        # y el máximo lo da el tamaño de letra (ver
        # effective_subtitle_margin_v). Todos los estilos llevan
        # MarginV 12 SALVO rubius (default 2026-11-19, todos los
        # clips a rubius): palabra por palabra blanca SOBRE el
        # gameplay, MarginV 900.
        self.assertEqual(subtitle_min_margin_v(), 0)
        self.assertEqual(subtitle_min_margin_v(load_config()), 0)
        for name, style in load_config()["subtitle_styles"].items():
            expected = 900 if name == "rubius" else 12
            self.assertEqual(style["margin_v"], expected, name)
        ref = load_config()["subtitle_styles"]["ref"]
        self.assertEqual(ref["margin_v"], 12)
        self.assertEqual(ref["fontsize"], 32)
        self.assertFalse(ref["bold"])
        self.assertFalse(ref["box"])
        rdjavi = load_config()["subtitle_styles"]["rdjavi"]
        self.assertEqual(rdjavi["margin_v"], 12)
        self.assertEqual(rdjavi["fontsize"], 64)
        self.assertEqual(load_config()["subtitles"]["default_style"],
                         "rubius")

    def test_margin_v_viejo_140_tapaba_la_webcam(self):
        # Con MarginV 140 y letra 32 px el texto queda justo en
        # 1740-1780 (borde de la barra): no solapa el gameplay.
        self.assertFalse(subtitles_overlap_webcam(140))
        # Con letra 48 px el mismo MarginV sube a 1720: solapa.
        self.assertTrue(subtitles_overlap_webcam(140, fontsize=48))
        # Con 230 (referencia vieja) el texto queda en 1650-1690,
        # en pleno gameplay: solapa los subtítulos propios de GTA 6.
        self.assertTrue(subtitles_overlap_webcam(230))
        self.assertTrue(subtitles_overlap_webcam(230, fontsize=32))
        # Con 1400 el borde superior subiría a la zona de cámara.
        self.assertTrue(subtitles_overlap_webcam(1400))
        # La posición v2 (12) queda en la barra, sin solapar.
        self.assertFalse(subtitles_overlap_webcam(12))
        self.assertFalse(subtitles_overlap_webcam(12, fontsize=64))

    def test_burn_corrige_el_margin_v_viejo_solo(self):
        # El clamp deja el texto entero en la barra 1740-1920:
        # máximo 180 - alto del texto (a 32 px: 140; a 64 px: 100).
        self.assertEqual(effective_subtitle_margin_v(140), 140)
        self.assertEqual(effective_subtitle_margin_v(140, load_config()),
                         140)
        self.assertEqual(effective_subtitle_margin_v(700), 140)
        # Un MarginV demasiado alto baja al máximo (no tapa el juego).
        self.assertLess(effective_subtitle_margin_v(1400), 1400)
        self.assertEqual(effective_subtitle_margin_v(230), 140)
        # La posición v2 (12) no se toca.
        self.assertEqual(effective_subtitle_margin_v(12, fontsize=64), 12)
        self.assertEqual(effective_subtitle_margin_v(230, fontsize=64),
                         100)

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
