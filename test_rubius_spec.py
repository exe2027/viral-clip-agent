"""Puerta rubius: banner + una palabra + SFX (caso scooter 2026-11-21).

El Short del scooter se publicó como "rubius" sin serlo: sin banner
amarillo, subtítulos de línea completa y sin SFX. La causa: el gate
solo medía presencia de blanco/amarillo con umbrales bajos (890 px
amarillos de la calle pasaban el banner) y no medía ni el ancho de
los subtítulos ni el audio. Estos tests fijan el spec:

- negativo (scooter): tiene que FALLAR con titulo_banner,
  subtitulos_una_palabra y sfx_presente;
- positivo (52h): tiene que PASAR entero.

Los SRT van incrustados como fixtures (los tests solo leen los dos
MP4 del workspace, nada más de runs/). Si los MP4 no existen, los
tests reales se saltan; los sintéticos siempre corren.
"""
import os
import subprocess
import tempfile
import unittest

from clipagent import final_review as fr
from clipagent.config import load_validator_config

RUNS = os.path.expanduser(
    "~/workspace/gta6-clips/runs/speed-52h-save-2026-11-21")
SCOOTER = os.path.join(
    RUNS, "job119",
    "c550054d0b6c64deff85614bdf241a7d_ishowspeed_scooter_abuela.mp4")
FINAL52 = os.path.join(
    RUNS, "clip_speed_52h_save_final.mp4")

SCOOTER_SRT = """1
00:00:00,000 --> 00:00:04,920
Hey bro, give me that scooter bro, give me that scooter

2
00:00:07,040 --> 00:00:13,480
Bro, give me the fucking hey, you know, fuck you think you're going

3
00:00:22,670 --> 00:00:29,670
No, see I feel bad now, bro. I feel bad, bro. Now I go like I'll have it back have it back have it back have it back

4
00:00:29,670 --> 00:00:35,150
I'm sorry. I'm sorry. I'm sorry. Come on. I'm sorry, bro. I

5
00:00:35,990 --> 00:00:38,670
Ain't even not like no, get up, get up, get up, get up

6
00:00:40,190 --> 00:00:45,510
Not so I feel bad, bro. I feel bad, bro. Get up, bro

7
00:00:46,990 --> 00:00:48,990
Here you can have your scooter back
"""

FINAL52_SRT = """1
00:00:00,000 --> 00:00:11,720
load that one no what what you just bro you bro i'm gone about to end my stream bro you just

2
00:00:11,720 --> 00:00:25,240
told me to press no, i told you to load it bro i bro what the hell bro he's bro he pointed at

3
00:00:25,240 --> 00:00:30,700
it and pressed loaded bro so my brain bro obviously my bro i haven't went to sleep bro so my brain

4
00:00:30,700 --> 00:00:35,080
pressed it bro okay well you can still go back and do the pre- finale just can't go back to

5
00:00:35,080 --> 00:00:45,760
pre-story oh my gosh bro i just deleted the game obviously i pressed it cuz he pointed at to it bro
"""


def _sfx_asset():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "clipagent", "sfx", "vine_boom.wav")


def _make_audio_clip(path, with_sfx):
    """MP4 4 s con tono (y vine_boom a t=1 s si with_sfx)."""
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "color=c=0x404040:s=320x240:d=4",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=4"]
    if with_sfx:
        cmd += ["-i", _sfx_asset(),
                "-filter_complex",
                "[1:a]volume=0.1[base];"
                "[2:a]adelay=1000:all=1[boom];"
                "[base][boom]amix=inputs=2:normalize=0[aout]",
                "-map", "0:v", "-map", "[aout]"]
    else:
        cmd += ["-filter_complex", "[1:a]volume=0.1[aout]",
                "-map", "0:v", "-map", "[aout]"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", "128k", path]
    subprocess.run(cmd, check=True, timeout=60)
    return path


class TestNcc(unittest.TestCase):
    def test_self_correlation_is_one(self):
        from clipagent.audio import decode_audio
        tpl, sr = decode_audio(_sfx_asset())
        corr, lag = fr._ncc_best(tpl, tpl, sr)
        self.assertAlmostEqual(corr, 1.0, places=2)
        self.assertAlmostEqual(lag, 0.0, places=2)

    def test_silence_is_zero(self):
        import numpy as np
        from clipagent.audio import decode_audio
        tpl, sr = decode_audio(_sfx_asset())
        corr, _lag = fr._ncc_best(np.zeros(16000 * 4, dtype=np.float64),
                                  tpl[:16000], sr)
        self.assertLess(corr, 0.1)


class TestDetectSfx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_con_sfx_encontrado(self):
        clip = _make_audio_clip(
            os.path.join(self.tmp.name, "con.mp4"), True)
        res = fr.detect_sfx_in_clip(clip)
        self.assertTrue(res["found"], res)
        self.assertEqual(res["effect"], "vine_boom")
        self.assertGreater(res["corr"], 0.5)
        self.assertAlmostEqual(res["lag_s"], 1.0, delta=0.2)

    def test_sin_sfx_no_encontrado(self):
        clip = _make_audio_clip(
            os.path.join(self.tmp.name, "sin.mp4"), False)
        res = fr.detect_sfx_in_clip(clip)
        self.assertFalse(res["found"], res)
        self.assertLess(res["corr"], 0.5)

    def test_sin_audio_no_encontrado(self):
        clip = os.path.join(self.tmp.name, "mudo.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=0x404040:s=320x240:d=2",
             "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", clip],
            check=True, timeout=60)
        res = fr.detect_sfx_in_clip(clip)
        self.assertFalse(res["found"], res)
        self.assertIn("sin pista de audio", res["motivo"])


@unittest.skipUnless(os.path.isfile(SCOOTER)
                     and os.path.isfile(FINAL52),
                     "MP4 reales del run de Speed no presentes")
class TestRubiusRealClips(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.scooter_srt = os.path.join(self.tmp.name, "scooter.srt")
        with open(self.scooter_srt, "w", encoding="utf-8") as f:
            f.write(SCOOTER_SRT)
        self.final52_srt = os.path.join(self.tmp.name, "52h.srt")
        with open(self.final52_srt, "w", encoding="utf-8") as f:
            f.write(FINAL52_SRT)
        self.vcfg = load_validator_config(None)
        self.recipe = self.vcfg["templates"]["rubius"]

    def _gate(self, clip, srt):
        return fr.template_gate(
            clip, self.vcfg, self.recipe, srt_path=srt,
            out_dir=os.path.join(self.tmp.name, "gate"))

    def test_scooter_falla_las_tres(self):
        # El Short publicado: sin banner, subs de línea completa
        # y sin SFX. Tiene que fallar exactamente en esas tres.
        res = self._gate(SCOOTER, self.scooter_srt)
        self.assertFalse(res["pasa"], res["motivo"])
        for name in ("titulo_banner", "subtitulos_una_palabra",
                     "sfx_presente"):
            self.assertIn(name, res["fallos"], res["checks"])
        self.assertEqual(
            set(res["fallos"]),
            {"titulo_banner", "subtitulos_una_palabra",
             "sfx_presente"},
            res["checks"])

    def test_52h_pasa(self):
        # El final renderizado con el motor rubius: banner,
        # palabra por palabra y SFX (boom 0.994, riser 0.979).
        res = self._gate(FINAL52, self.final52_srt)
        self.assertTrue(res["pasa"], res["motivo"])
        self.assertEqual(res["fallos"], [])


if __name__ == "__main__":
    unittest.main()
