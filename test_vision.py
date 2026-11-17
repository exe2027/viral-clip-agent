#!/usr/bin/env python3
"""Tests de vision.py: Gemini elige el mejor frame, con fallback."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import vision
from vision import _parse_choice, choose_best_frame

FRAMES = [
    {"path": "/tmp/f1.jpg", "role": "antes del payoff", "ts_clip": 15.0},
    {"path": "/tmp/f2.jpg", "role": "payoff", "ts_clip": 20.0},
    {"path": "/tmp/f3.jpg", "role": "después del payoff", "ts_clip": 25.0},
]


class TestParseChoice(unittest.TestCase):
    def test_valid_json(self):
        choice = _parse_choice(
            '{"best": 3, "ranking": [3, 1, 2], "reason": "se ve la risa"}', 3)
        self.assertEqual(choice, {"best": 3, "ranking": [3, 1, 2],
                                  "reason": "se ve la risa"})

    def test_json_con_texto_alrededor(self):
        choice = _parse_choice(
            'Claro:\n{"best": 1, "ranking": [1, 2, 3], "reason": "nítido"}\n', 3)
        self.assertEqual(choice["best"], 1)

    def test_ranking_invalido_se_repara(self):
        choice = _parse_choice('{"best": 2, "ranking": [2, 2], "reason": "x"}', 3)
        self.assertEqual(choice["ranking"], [2, 1, 3])

    def test_best_fuera_de_rango(self):
        self.assertIsNone(_parse_choice('{"best": 9, "ranking": [9, 1, 2]}', 3))

    def test_sin_json(self):
        self.assertIsNone(_parse_choice("no sé cuál es mejor", 3))


class TestChooseBestFrame(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp())
        self.frames = []
        for i, role in enumerate(
                ["antes del payoff", "payoff", "después del payoff"], start=1):
            path = self.tmp / f"f{i}.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0fake")
            self.frames.append({"path": str(path), "role": role,
                                "ts_clip": float(10 + 5 * i)})

    def test_sin_key_cae_al_payoff(self):
        with mock.patch("vision.get_api_key", return_value=None):
            result = choose_best_frame(self.frames, title="Errar un tiro")
        self.assertEqual(result["vision"], "skipped")
        self.assertEqual(result["best_index"], 1)  # payoff
        self.assertEqual(result["best_path"], self.frames[1]["path"])

    def test_gemini_elige_el_tercero(self):
        with mock.patch("vision.get_api_key", return_value="test-key"), \
             mock.patch("vision._call_gemini", return_value=json.dumps(
                 {"best": 3, "ranking": [3, 1, 2],
                  "reason": "la cara se ve mejor"})):
            result = choose_best_frame(self.frames, title="Errar un tiro")
        self.assertEqual(result["vision"], "gemini")
        self.assertEqual(result["best_index"], 2)
        self.assertEqual(result["best_path"], self.frames[2]["path"])
        self.assertEqual(result["ranking"], [3, 1, 2])

    def test_error_de_api_cae_al_payoff(self):
        with mock.patch("vision.get_api_key", return_value="test-key"), \
             mock.patch("vision._call_gemini",
                        side_effect=RuntimeError("Gemini HTTP 400: bad key")):
            result = choose_best_frame(self.frames, title="Errar un tiro")
        self.assertEqual(result["vision"], "error")
        self.assertEqual(result["best_index"], 1)
        self.assertIn("400", result["reason"])


class TestThumbnailsVision(unittest.TestCase):
    def test_cover_sin_vision_es_payoff(self):
        from thumbnails import instagram_cover
        with mock.patch("thumbnails.shutil.copy2"):
            cover = instagram_cover(FRAMES, out_path="/tmp/cover-test.jpg")
        self.assertEqual(cover["source_role"], "payoff")

    def test_cover_con_vision_usa_el_frame_elegido(self):
        from thumbnails import choose_cover_frame
        tmp = Path(__import__("tempfile").mkdtemp())
        frames = []
        for i, role in enumerate(
                ["antes del payoff", "payoff", "después del payoff"], start=1):
            path = tmp / f"f{i}.jpg"
            path.write_bytes(b"\xff\xd8\xff\xe0fake")
            frames.append({"path": str(path), "role": role, "ts_clip": float(10 + 5 * i)})
        with mock.patch("vision.get_api_key", return_value="test-key"), \
             mock.patch("vision._call_gemini", return_value=json.dumps(
                 {"best": 1, "ranking": [1, 2, 3], "reason": "gameplay claro"})):
            frame, result = choose_cover_frame(
                frames, title="Qué jugada", use_vision=True)
        self.assertEqual(frame["path"], frames[0]["path"])
        self.assertEqual(result["vision"], "gemini")


if __name__ == "__main__":
    unittest.main()
