#!/usr/bin/env python3
"""Tests §8 grupo 10 (spec títulos-verdad 2026-11-25).

El SRT generado es la transcripción literal: el texto que sale
en pantalla es exactamente lo que dijo el streamer, sin texto
inventado, y la llamada 1 (burn=None) no quema nada.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.workers import subtitle_clip

SEGMENTS = []
for _start, _end, _text in [
        (0.0, 3.5, "vamos a seguir con la misión"),
        (3.5, 7.0, "había que hacerlo de Brian para saltar el banco"),
        (7.0, 9.5, "eso va a estar guapo")]:
    _words = _text.split()
    _step = (_end - _start) / len(_words)
    SEGMENTS.append({
        "start": _start, "end": _end, "text": _text,
        "words": [{"word": w, "start": _start + i * _step,
                   "end": _start + (i + 1) * _step}
                  for i, w in enumerate(_words)]})
LITERAL = " ".join(s["text"] for s in SEGMENTS)


class TestSrtIsLiteralTranscription(unittest.TestCase):
    def _fake_transcribe(self, *a, **kw):
        return {"segments": SEGMENTS,
                "word_count": len(LITERAL.split())}, {}

    def test_srt_es_la_transcripcion_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "clip.mp4")
            with open(video, "wb") as f:
                f.write(b"fake")
            with mock.patch("clipagent.workers._do_transcribe",
                            side_effect=self._fake_transcribe):
                out = subtitle_clip(video, burn=None)
            self.assertEqual(out["srt"],
                             os.path.splitext(video)[0] + ".srt")
            with open(out["srt"], encoding="utf-8") as f:
                srt = f.read()
            # El SRT agrupa las palabras en líneas cortas: el texto
            # concatenado de todos los bloques es la transcripción
            # literal, palabra por palabra.
            lines = [ln.strip() for ln in srt.splitlines()
                     if ln.strip() and not ln.strip().isdigit()
                     and "-->" not in ln]
            self.assertEqual(" ".join(lines), LITERAL)
            # Nada inventado: ninguna palabra fuera de la
            # transcripción aparece en el SRT.
            for word in ("increíble", "épico", "locura", "chat",
                         "explotó"):
                self.assertNotIn(word, srt.lower())
            self.assertNotIn("burned", out)
            # .transcript.json guarda la misma transcripción.
            with open(out["transcript"], encoding="utf-8") as f:
                stored = json.load(f)
            self.assertEqual(
                [s["text"] for s in stored["segments"]],
                [s["text"] for s in SEGMENTS])

    def test_srt_existente_se_reutiliza_sin_retranscribir(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = os.path.join(tmp, "clip.mp4")
            with open(video, "wb") as f:
                f.write(b"fake")
            srt = os.path.splitext(video)[0] + ".srt"
            with open(srt, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:03,500\n"
                        "vamos a seguir con la misión\n")
            tpath = os.path.splitext(srt)[0] + ".transcript.json"
            with open(tpath, "w", encoding="utf-8") as f:
                json.dump({"segments": [SEGMENTS[0]],
                           "word_count": 5}, f)
            with mock.patch(
                    "clipagent.workers._do_transcribe",
                    side_effect=AssertionError(
                        "no debe re-transcribir")):
                out = subtitle_clip(video, srt=srt, burn=None)
            self.assertEqual(out["srt"], srt)
            self.assertTrue(out.get("reused_srt", False)
                            or out["srt"] == srt)
            with open(out["srt"], encoding="utf-8") as f:
                self.assertIn("vamos a seguir con la misión",
                              f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
