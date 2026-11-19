#!/usr/bin/env python3
"""Pruebas del mezclador SFX (SFX-AUDIT.md §3-4, 2026-11-18).

Reglas verificadas: boom en el payoff s=20 solo con dip de silencio,
riser en s=18-20 con el boom (regla 8), nunca sobre gritos del
streamer ni en hook/loop, máx. 2 por clip, objetivo RMS por efecto
(-11 dBFS el boom; el resto según su target en sfx_mix.py), ducking
-6 dB sobre la ventana audible (el riser no lleva ducking), techo
post-mezcla <= -10 dBFS, lift general de volumen con compresión.

NOTA DE MEDICIÓN (2026-11-19): la decodificación pipe f32le
(``ffmpeg -f f32le -`` + ``np.frombuffer``) da valores imposibles
en esta VM (pico 1,03 > 1,0 en un archivo 16-bit). Por eso `_pcm`
usa ``decode_audio`` (ffmpeg a WAV 16-bit + módulo wave), la vía
fiable del repo. Los números espectrales del informe anterior
(boom 2908 con fracción ≤120 Hz 0,86) salieron de esa vía rota;
la medición nativa da 0,46: el 2908 es un impacto de tráiler con
ataque brillante, no un seno puro, y el test de firma se reescribió
para eso.
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.audio import decode_audio, rms_per_second
from clipagent.sfx_mix import (EFFECTS, _sfx_levels, decide_sfx,
                               load_manifest, mix_sfx, sfx_duration,
                               sfx_gain_db, sfx_path)

HERE = os.path.dirname(os.path.abspath(__file__))
P7775 = os.path.join(HERE, "runs", "2932415021-p7775", "clips",
                     "clip_01_t02h09m15s_s17.36_r3h1.mp4")
P7775_TR = os.path.join(HERE, "runs", "2932415021-p7775",
                        "seg7775.transcript.json")
P3220 = os.path.join(HERE, "runs", "2932415021-p3220", "clips",
                     "clip_01_t53m20s_s14.3_r7h4.mp4")
P3220_TR = os.path.join(HERE, "runs", "2932415021-p3220", "clips",
                        "clip_01_t53m20s_s14.3_r7h4.transcript.json")


def _load_transcript(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _make_wav(path, levels_dbfs, sr=16000):
    """WAV de 48 s: un nivel RMS por segundo (seno 440 Hz)."""
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        for db in levels_dbfs:
            amp = 10 ** (db / 20.0)
            t = np.arange(sr) / sr
            seg = (amp * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
            w.writeframes(seg.tobytes())


def _peak_dbfs(path):
    r = subprocess.run(["ffmpeg", "-i", path, "-af", "volumedetect",
                        "-vn", "-sn", "-dn", "-f", "null", "/dev/null"],
                       capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "max_volume" in line:
            return float(line.split(":")[1].strip().replace(" dB", ""))
    raise RuntimeError(f"sin pico: {path}")


class TestManifest(unittest.TestCase):
    def test_pack_completo_y_licencias(self):
        manifest = load_manifest()
        files = manifest["files"]
        self.assertEqual(set(files),
                         {EFFECTS[e]["file"] for e in EFFECTS})
        for effect in EFFECTS:
            info = files[EFFECTS[effect]["file"]]
            self.assertTrue(info.get("license"), effect)
            self.assertTrue(info.get("origin"), effect)
            self.assertTrue(os.path.isfile(sfx_path(effect)), effect)

    def test_archivos_en_su_objetivo_rms(self):
        # Objetivo RMS por efecto (sfx_mix.py), no pico -11 dBFS:
        # el boom real Mixkit 2908 tiene pico -3,6 dBFS y el test de
        # pico fallaba a propósito con el archivo nuevo.
        for effect in EFFECTS:
            loud_rms, peak_db = _sfx_levels(effect)
            target = EFFECTS[effect]["target_rms_dbfs"]
            gain = sfx_gain_db(effect)
            # La ganancia del mezclador deja la ventana audible en el
            # objetivo (salvo recorte por el techo de pico).
            self.assertAlmostEqual(loud_rms + gain, target, delta=1.5,
                                   msg=effect)
            self.assertLessEqual(abs(gain), 12.0, effect)
            # El pico tras la ganancia no supera -1 dBFS.
            self.assertLessEqual(peak_db + gain, -1.0 + 0.1, effect)
            dur = sfx_duration(effect)
            self.assertGreater(dur, 0.5, effect)
            self.assertLessEqual(dur, 2.6, effect)

    def test_boom_y_riser_ya_en_objetivo(self):
        # Los dos archivos preparados a mano ya están en su objetivo:
        # la ganancia del mezclador es ~0 dB para ellos.
        self.assertAlmostEqual(sfx_gain_db("vine_boom"), 0.0, delta=1.5)
        self.assertAlmostEqual(sfx_gain_db("riser"), 0.0, delta=1.5)

    def test_ding_y_whoosh_ya_en_objetivo(self):
        # Ding y whoosh re-procesados 2026-11-19 (pedido del usuario:
        # subirlos para que se noten): tanh soft-clip + compresión
        # ascendente para bajar el crest a ~10 dB y objetivo RMS
        # -11,5 dBFS en su ventana audible. La ganancia del
        # mezclador es ~0 dB (ding -0,9 dB por el sobreimpulso de
        # remuestreo del pico, whoosh -0,4 dB).
        self.assertAlmostEqual(sfx_gain_db("ding"), 0.0, delta=1.5)
        self.assertAlmostEqual(sfx_gain_db("whoosh"), 0.0, delta=1.5)
        # Y el objetivo nuevo es -11,5 dBFS, no -13.
        self.assertEqual(EFFECTS["ding"]["target_rms_dbfs"], -11.5)
        self.assertEqual(EFFECTS["whoosh"]["target_rms_dbfs"], -11.5)


class TestSfxFiles(unittest.TestCase):
    def _pcm(self, path, sr=16000):
        # Vía fiable: decode_audio (WAV + wave). La vía pipe f32le
        # da valores imposibles en esta VM (ver docstring).
        audio, _ = decode_audio(path, sample_rate=sr)
        return audio.astype(np.float32)

    def test_vine_boom_firma_espectral(self):
        # El boom REAL (Mixkit 2908, impacto de tráiler) no es un
        # seno puro: su fracción ≤120 Hz es 0,46, no >0,7 como el
        # sintetizado. Lo que lo define: golpe a -11 dBFS RMS en el
        # primer segundo, energía concentrada bajo 500 Hz y ZCR bajo
        # (impacto, no voz ni ruido). El ataque brillante (>500 Hz)
        # es lo que lo hace audible en el parlante del celular.
        x = self._pcm(sfx_path("vine_boom"))
        seg = x[:16000]
        rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
        self.assertAlmostEqual(20 * math.log10(rms), -11.0, delta=1.5)
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1 / 16000)
        below_500 = spec[freqs <= 500].sum() / spec.sum()
        self.assertGreater(below_500, 0.6)
        zcr = ((seg[:-1] * seg[1:]) < 0).mean()
        self.assertLess(zcr, 0.06)

    def test_riser_sube(self):
        # El riser sube en nivel y en brillo (centroide) con el tiempo.
        x = self._pcm(sfx_path("riser"))
        def stats(seg):
            rms = float(np.sqrt(np.mean(seg.astype(np.float64) ** 2)))
            spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
            freqs = np.fft.rfftfreq(len(seg), 1 / 16000)
            cent = (freqs * spec).sum() / spec.sum()
            return rms, cent
        rms0, cent0 = stats(x[:16000])
        rms1, cent1 = stats(x[16000:32000])
        self.assertGreater(rms1, rms0)
        self.assertGreater(cent1, cent0)

    def test_bruh_es_voz(self):
        # El bruh es voz (centroide alto, ZCR de voz), no un grave.
        x = self._pcm(sfx_path("bruh"))
        seg = x[:16000]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        freqs = np.fft.rfftfreq(len(seg), 1 / 16000)
        cent = (freqs * spec).sum() / spec.sum()
        self.assertGreater(cent, 800)


class TestDecide(unittest.TestCase):
    def test_boom_y_riser_en_los_dos_clips(self):
        # Payoff + dip -> boom en s=20 Y riser en s=18-20 (regla 8).
        for clip, tr in ((P7775, P7775_TR), (P3220, P3220_TR)):
            if not os.path.isfile(clip):
                self.skipTest(f"sin clip: {clip}")
            events = decide_sfx(clip, transcript=_load_transcript(tr))
            self.assertEqual(len(events), 2, clip)
            by_effect = {e["effect"]: e for e in events}
            self.assertEqual(by_effect["vine_boom"]["start"], 20.0)
            self.assertEqual(by_effect["riser"]["start"], 18.0)

    def test_sin_dip_no_hay_boom_ni_riser(self):
        # Audio plano a -18 dBFS los 48 s: sin dip, sin boom, sin riser.
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "plano.wav")
            _make_wav(wav, [-18.0] * 48)
            self.assertEqual(decide_sfx(wav), [])

    def test_dip_sintetico_si_hay_boom_y_riser(self):
        # Fuerte (-15) hasta s=19, silencio (-40) en s=20, fuerte después.
        levels = [-15.0] * 20 + [-40.0] + [-15.0] * 27
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "dip.wav")
            _make_wav(wav, levels)
            events = decide_sfx(wav)
            self.assertEqual(len(events), 2)
            by_effect = {e["effect"]: e for e in events}
            self.assertEqual(by_effect["vine_boom"]["start"], 20.0)
            self.assertEqual(by_effect["riser"]["start"], 18.0)

    def test_guardas_hook_y_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "dip.wav")
            levels = [-15.0] * 20 + [-40.0] + [-15.0] * 27
            _make_wav(wav, levels)
            # Payoff en el hook (s=2) o en el loop (s=47): nada.
            self.assertEqual(decide_sfx(wav, payoff_s=2.0), [])
            self.assertEqual(decide_sfx(wav, payoff_s=47.0), [])

    def test_fail_con_grito_cerca_no_se_pone(self):
        if not os.path.isfile(P7775):
            self.skipTest("sin clip p7775")
        tr = _load_transcript(P7775_TR)
        # Gritos del streamer a s=16-19: fail a s=17 no lleva trombón.
        events = decide_sfx(P7775, transcript=tr, fail_s=17.0)
        self.assertEqual([e["effect"] for e in events],
                         ["vine_boom", "riser"])
        # Fail a s=30, lejos de gritos: sí lleva trombón, pero con el
        # presupuesto por defecto (2) el par del payoff gana; con 3
        # huecos entra el trombón.
        events = decide_sfx(P7775, transcript=tr, fail_s=30.0)
        self.assertEqual([e["effect"] for e in events],
                         ["vine_boom", "riser"])
        events = decide_sfx(P7775, transcript=tr, fail_s=30.0, max_sfx=3)
        self.assertEqual([e["effect"] for e in events],
                         ["vine_boom", "riser", "sad_trombone"])

    def test_maximo_dos_efectos(self):
        if not os.path.isfile(P7775):
            self.skipTest("sin clip p7775")
        tr = _load_transcript(P7775_TR)
        events = decide_sfx(P7775, transcript=tr, fail_s=30.0,
                            max_sfx=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["effect"], "vine_boom")

    def test_boom_no_lo_bloquea_el_retorno_tras_el_silencio(self):
        # Regresión (2026-11-18): en el render re-codificado de p7775
        # el detector marca s=21 como "grito" porque la mediana de
        # los 10 s anteriores incluye el silencio del payoff (s=20),
        # y el retorno a la voz normal parece un salto de +8 dB.
        # No es un grito: es la voz normal reanudándose. El boom
        # (ventana audible 1,0 s: 20,0-21,0) no lo tapa y se pone.
        from clipagent.audio import streamer_shout_spikes
        levels = ([-30.0] * 13
                  + [-41.0, -31.0, -17.0, -17.0, -22.0, -17.0, -19.0,
                     -40.0]
                  + [-17.0] * 6 + [-20.0] * 21)
        tr = {"segments": [{"start": 15.0, "end": 27.0, "text": "x"}]}
        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "caso.wav")
            _make_wav(wav, levels)
            shouts = streamer_shout_spikes(rms_per_second(wav), tr)
            self.assertIn(21, shouts)  # el artefacto existe de verdad
            events = decide_sfx(wav, transcript=tr)
            by_effect = {e["effect"]: e for e in events}
            self.assertEqual(by_effect["vine_boom"]["start"], 20.0)
            self.assertEqual(by_effect["riser"]["start"], 18.0)


class TestMix(unittest.TestCase):
    def test_boom_en_s20_sin_tapar_la_voz(self):
        if not os.path.isfile(P7775):
            self.skipTest("sin clip p7775")
        tr = _load_transcript(P7775_TR)
        events = decide_sfx(P7775, transcript=tr)
        self.assertEqual(len(events), 2)
        orig = {int(s): db for s, db in rms_per_second(P7775)}
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "mezclado.mp4")
            _, info = mix_sfx(P7775, events, out)
            self.assertTrue(os.path.isfile(out))
            mixed = {int(s): db for s, db in rms_per_second(out)}
            # El segundo del boom suena a ~-11 dBFS RMS (objetivo).
            self.assertGreaterEqual(mixed[20], -13.0)
            self.assertLessEqual(mixed[20], -10.0)
            # La voz de s=22-26 sube con el lift (compand ascendente:
            # la voz fuerte sube ~+1,5 dB, la tranquila ~+4 dB) pero
            # no se tapa ni se bombea: delta entre +1 y +9 dB.
            for s in range(22, 27):
                delta = mixed[s] - orig[s]
                self.assertGreaterEqual(delta, 1.0, msg=f"s={s}")
                self.assertLessEqual(delta, 9.0, msg=f"s={s}")
            # El riser sube s=19 (voz + riser) sin tapar la voz de
            # s=22 en adelante; s=18 (grito + inicio del riser, casi
            # mudo) no se dispara.
            self.assertLessEqual(mixed[18] - orig[18], 3.0)
            # Duración intacta y sin clipping.
            self.assertAlmostEqual(len(mixed), len(orig), delta=1)
            self.assertLessEqual(_peak_dbfs(out), -1.0)

    def test_sin_eventos_con_lift_sube_el_volumen(self):
        if not os.path.isfile(P7775):
            self.skipTest("sin clip p7775")
        orig = {int(s): db for s, db in rms_per_second(P7775)}
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "lift.mp4")
            _, info = mix_sfx(P7775, [], out, lift=True)
            self.assertEqual(info["events"], [])
            mixed = {int(s): db for s, db in rms_per_second(out)}
            self.assertEqual(len(mixed), len(orig))
            # La voz sube con el lift, sin pasarse de +9 dB.
            for s in (22, 23, 24, 25):
                delta = mixed[s] - orig[s]
                self.assertGreaterEqual(delta, 1.0, msg=f"s={s}")
                self.assertLessEqual(delta, 9.0, msg=f"s={s}")
            self.assertLessEqual(_peak_dbfs(out), -1.0)

    def test_sin_eventos_sin_lift_copia_sin_tocar(self):
        if not os.path.isfile(P7775):
            self.skipTest("sin clip p7775")
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "copia.mp4")
            _, info = mix_sfx(P7775, [], out, lift=False)
            self.assertEqual(info["events"], [])
            self.assertTrue(os.path.isfile(out))
            orig = rms_per_second(P7775)
            mixed = rms_per_second(out)
            self.assertEqual(len(mixed), len(orig))


if __name__ == "__main__":
    unittest.main()
