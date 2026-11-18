#!/usr/bin/env python3
"""Frases hype del streamer: detección en transcripciones."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipagent.streamer_hype import find_streamer_hype, transcript_text


class TestStreamerHype(unittest.TestCase):
    def test_frases_del_usuario(self):
        # Las que el usuario pegó en el chat: WTF, OMG, Dios mío.
        hits = find_streamer_hype("¡Dios mío! WTF, OMG, ¿qué es esto?")
        self.assertEqual(hits["count"], 3)
        self.assertIn("dios mío", hits["phrases"])
        self.assertIn("wtf", hits["phrases"])
        self.assertIn("omg", hits["phrases"])

    def test_frase_larga_y_letras_alargadas(self):
        hits = find_streamer_hype(
            "Nooo, no lo puedo creeer, esto es increíble")
        self.assertIn("no lo puedo creer", hits["phrases"])
        self.assertIn("esto es increíble", hits["phrases"])

    def test_sin_acentos_ni_mayusculas(self):
        hits = find_streamer_hype("DIOS MIO, NO ME LO CREO")
        self.assertIn("dios mío", hits["phrases"])
        self.assertIn("no me lo creo", hits["phrases"])

    def test_sin_hype(self):
        hits = find_streamer_hype(
            "Bueno, vamos a seguir con la misión tranquilamente.")
        self.assertEqual(hits["count"], 0)
        self.assertEqual(hits["phrases"], [])

    def test_texto_vacio_no_lanza(self):
        self.assertEqual(find_streamer_hype(""),
                         {"count": 0, "distinct": 0, "phrases": []})
        self.assertEqual(find_streamer_hype(None),
                         {"count": 0, "distinct": 0, "phrases": []})

    def test_transcript_text(self):
        tr = {"segments": [{"text": "Dios mío "},
                           {"text": "no lo puedo creer"}]}
        self.assertEqual(transcript_text(tr),
                         "Dios mío no lo puedo creer")
        self.assertEqual(transcript_text({"text": "WTF"}), "WTF")
        self.assertEqual(transcript_text({"no_speech": True}), "")
        self.assertEqual(transcript_text(None), "")


if __name__ == "__main__":
    unittest.main()
