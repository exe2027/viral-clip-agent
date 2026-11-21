#!/usr/bin/env python3
"""Pruebas de zoom por hype + SFX por defecto (2026-11-19).

Zoom: el punch-in va en ventanas de 4 s alrededor de los
segundos de hype detectados (gritos del streamer / explosión
del chat), no en la ventana fija 16-26 s; varias ventanas en
el mismo clip se encienden en unión. Sin hype, el default v2
fijo se mantiene.

SFX: la mezcla es por defecto en el render (vivo y VOD):
boom en el payoff + lift de volumen; desactivable con
``sfx.enabled: false``; si la mezcla falla, el clip sigue sin
SFX (nunca lo tumba).
"""
import math
import os
import subprocess
import tempfile
import unittest
import wave

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.config import load_config
from clipagent.render import (
    hype_punch_windows, hype_seconds_from_audio, _punch_window,
    _split_params, _split_rdjavi_graph, _punch_face_center,
    _fill_punch_face_centers, DEFAULTS,
)
from clipagent.workers import apply_sfx

HERE = os.path.dirname(os.path.abspath(__file__))
CLIP23_SEG = "/home/hatch/workspace/gta6-clips/runs/clip23-facecam-test/seg.mp4"
P7775 = os.path.join(HERE, "runs", "2932415021-p7775", "clips",
                     "clip_01_t02h09m15s_s17.36_r3h1.mp4")
P7775_TR = os.path.join(HERE, "runs", "2932415021-p7775",
                        "seg7775.transcript.json")


def _make_wav(path, levels_dbfs, sr=16000):
    """WAV de N segundos: un nivel RMS por segundo (seno 440 Hz)."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for db in levels_dbfs:
            amp = 0.9 * (10 ** (db / 20.0))
            n = sr
            frames = b"".join(
                (int(amp * 32767 * math.sin(2 * math.pi * 440 * i / sr))
                 ).to_bytes(2, "little", signed=True)
                for i in range(n))
            w.writeframes(frames)


class TestHypePunchWindows(unittest.TestCase):
    def test_un_segundo_da_ventana_de_4s(self):
        self.assertEqual(hype_punch_windows([20.0], 40.0),
                         [(19.0, 23.0)])

    def test_varios_se_ordenan_y_se_capan_a_3(self):
        wins = hype_punch_windows([20.0, 31.0, 5.0, 12.0], 40.0)
        self.assertEqual(len(wins), 3)
        self.assertEqual(wins[0], (4.0, 8.0))
        self.assertEqual(wins[1], (11.0, 15.0))
        self.assertEqual(wins[2], (19.0, 23.0))

    def test_se_fusionan_las_que_se_solapan(self):
        self.assertEqual(hype_punch_windows([20.0, 21.5], 40.0),
                         [(19.0, 24.5)])

    def test_se_recorta_al_inicio_y_al_final_del_clip(self):
        self.assertEqual(hype_punch_windows([0.5, 39.5], 40.0),
                         [(0.0, 4.0), (38.5, 40.0)])

    def test_sin_hype_no_hay_ventanas(self):
        self.assertEqual(hype_punch_windows([], 40.0), [])


class TestPunchWindowHype(unittest.TestCase):
    def test_con_hype_la_principal_es_la_primera_ventana(self):
        self.assertEqual(_punch_window({"hype_s": [20.0, 31.0]}),
                         (19.0, 23.0))

    def test_sin_hype_sigue_el_default_v2(self):
        self.assertEqual(_punch_window({}), (16.0, 26.0))
        self.assertEqual(_punch_window({"hype_s": []}), (16.0, 26.0))

    def test_explicito_manda_sobre_hype(self):
        self.assertEqual(
            _punch_window({"hype_s": [20.0],
                           "punch_in": {"t0": 5, "t1": 9}}),
            (5.0, 9.0))

    def test_false_desactiva_aunque_haya_hype(self):
        self.assertIsNone(_punch_window({"hype_s": [20.0],
                                         "punch_in": False}))


class TestSplitParamsHype(unittest.TestCase):
    def _box(self):
        return [10, 10, 60, 30]

    def test_con_hype_punch_y_game_zoom_se_anclan_al_momento(self):
        params = _split_params(self._box(), {"hype_s": [20.0, 31.0]})
        self.assertEqual(params["punch_in"], {"t0": 19.0, "t1": 23.0})
        self.assertEqual(params["punch_windows"],
                         [{"t0": 19.0, "t1": 23.0},
                          {"t0": 30.0, "t1": 34.0}])
        self.assertEqual(params["game_zoom"], {"t0": 17.0, "t1": 23.0})

    def test_sin_hype_no_hay_punch_windows(self):
        params = _split_params(self._box(), {})
        self.assertIsNone(params["punch_in"])
        self.assertNotIn("punch_windows", params)
        self.assertIsNone(params["game_zoom"])


class TestRdjaviGraphMultiWindow(unittest.TestCase):
    def _graph(self, params):
        return _split_rdjavi_graph("0:v", "vout", params, 1920, 1080, {})

    def _params(self, **extra):
        p = {"box": [10, 10, 60, 30], "split_variant": "rdjavi",
             "punch_in": {"t0": 19.0, "t1": 23.0, "zoom": 1.30}}
        p.update(extra)
        return p

    def test_una_ventana_usa_el_enable_simple(self):
        g = self._graph(self._params())
        self.assertIn("gte(t,19)*lt(t,23)", g)
        self.assertIn("lt(t,19)+gte(t,23)", g)

    def test_varias_ventanas_se_encienden_en_union(self):
        g = self._graph(self._params(punch_windows=[
            {"t0": 19.0, "t1": 23.0}, {"t0": 30.0, "t1": 34.0}]))
        self.assertIn("(gte(t,19)*lt(t,23))+(gte(t,30)*lt(t,34))", g)
        self.assertIn("(lt(t,19)+gte(t,23))*(lt(t,30)+gte(t,34))", g)

    def test_sin_punch_no_hay_rama_con_zoom(self):
        g = self._graph(self._params(punch_in=False))
        self.assertNotIn("[camZ]", g)


class TestHypeSecondsFromAudio(unittest.TestCase):
    def test_detecta_el_grito_cerca_del_pico(self):
        with tempfile.TemporaryDirectory() as d:
            wav = os.path.join(d, "a.wav")
            # 40 s tranquilos (-40 dBFS) con un grito (-12 dBFS)
            # en s=19-20 (el pico está en 20).
            levels = [-40.0] * 40
            levels[19] = -12.0
            levels[20] = -12.0
            _make_wav(wav, levels)
            secs = hype_seconds_from_audio(wav, 20.0, radius_s=10.0)
            self.assertIn(19, secs)

    def test_sin_grito_devuelve_vacio_sin_romper(self):
        with tempfile.TemporaryDirectory() as d:
            wav = os.path.join(d, "a.wav")
            _make_wav(wav, [-40.0] * 40)
            self.assertEqual(hype_seconds_from_audio(wav, 20.0), [])

    def test_archivo_inexistente_devuelve_vacio(self):
        self.assertEqual(
            hype_seconds_from_audio("/no/existe.mp4", 20.0), [])


class TestPunchFaceCenter(unittest.TestCase):
    """El punch-in se centra en la CARA, no en el centro de la
    caja de la webcam (clip 23, 2026-11-19: la cara de TheGrefg
    está abajo de su caja —centro de cara y~301 de caja 103-354—
    y el zoom centrado en la caja le cortaba la barbilla)."""

    @unittest.skipUnless(os.path.isfile(CLIP23_SEG),
                         "falta el fixture clip23 seg.mp4")
    def test_centro_en_la_cara_no_en_la_caja(self):
        center = _punch_face_center(CLIP23_SEG, 0.0, (13.0, 17.0),
                                    1920, 1080, dict(DEFAULTS))
        self.assertIsNotNone(center)
        # Cara medida con YuNet en el clip 23: centro ~(1719, 301)
        # en px originales; el centro de la caja es (1717, 228).
        self.assertGreater(center[0], 1600)
        self.assertLess(center[0], 1850)
        self.assertGreater(center[1], 260)
        self.assertLess(center[1], 350)

    @unittest.skipUnless(os.path.isfile(CLIP23_SEG),
                         "falta el fixture clip23 seg.mp4")
    def test_fill_pone_cx_cy_en_muestra(self):
        cfg = dict(DEFAULTS)
        cfg["hype_s"] = [14, 23, 24, 25]
        params = _split_params([127.0, 8.58, 32.17, 20.9], cfg)
        self.assertNotIn("cx", params["punch_in"])
        segs = [{"start": 0.0, "end": 40.0, "layout": "split",
                 "params": params, "reason": "prueba"}]
        _fill_punch_face_centers(CLIP23_SEG, 0.0, segs, cfg)
        punch = segs[0]["params"]["punch_in"]
        self.assertIsInstance(punch, dict)
        self.assertIn("cx", punch)
        self.assertIn("cy", punch)
        # Centro de cara (1719, 301) en coords de muestra 160x90.
        self.assertGreater(punch["cx"], 130)
        self.assertLess(punch["cx"], 160)
        self.assertGreater(punch["cy"], 20)
        self.assertLess(punch["cy"], 32)

    def test_sin_cara_desactiva_el_punch(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "negro.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=320x240:r=30:d=5",
                 "-f", "lavfi", "-i", "sine=frequency=440:d=5",
                 "-shortest", src],
                check=True)
            segs = [{"start": 0.0, "end": 5.0, "layout": "split",
                     "params": {"punch_in": {"t0": 1.0, "t1": 4.0}},
                     "reason": "prueba"}]
            _fill_punch_face_centers(src, 0.0, segs, dict(DEFAULTS))
            self.assertFalse(segs[0]["params"]["punch_in"])


class TestApplySfx(unittest.TestCase):
    @unittest.skipUnless(os.path.isfile(P7775),
                         "falta el fixture p7775 en runs/")
    def test_por_defecto_mezcla_y_el_boom_se_oye(self):
        cfg = load_config()
        self.assertTrue((cfg.get("sfx") or {}).get("enabled", False))
        with tempfile.TemporaryDirectory() as d:
            out, info = apply_sfx(
                P7775, transcript_path=P7775_TR, payoff_s=20.0,
                cfg=cfg, out_path=os.path.join(d, "mezclado.mp4"))
            self.assertTrue(os.path.isfile(out))
            self.assertTrue(info.get("events"))
            # El boom del payoff tiene que quedar audible: RMS del
            # segundo mezclado cerca de -11 dBFS (el primer A/B
            # con -22,6 dBFS NO se oyó en el celular).
            from clipagent.audio import rms_per_second
            mixed = dict(rms_per_second(out))
            boom_sec = int(info["events"][0]["start"])
            self.assertGreater(mixed[boom_sec], -15.0)

    def test_desactivado_devuelve_el_original_sin_tocar(self):
        cfg = {"sfx": {"enabled": False}}
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "a.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                 "color=c=black:s=320x240:r=30:d=3",
                 "-f", "lavfi", "-i", "sine=frequency=440:d=3",
                 "-t", "3", "-c:v", "libx264", "-c:a", "aac", src],
                check=True)
            out, info = apply_sfx(src, cfg=cfg)
            self.assertEqual(out, src)
            self.assertFalse(info.get("enabled"))

    def test_fallo_de_mezcla_no_tumba_el_clip(self):
        out, info = apply_sfx("/no/existe.mp4", cfg={})
        self.assertEqual(out, "/no/existe.mp4")
        self.assertIn("error", info)


if __name__ == "__main__":
    unittest.main()
