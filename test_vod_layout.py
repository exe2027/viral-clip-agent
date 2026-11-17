#!/usr/bin/env python3
"""Pruebas de 7.4: facecam detectada UNA vez por VOD y guardada en
runs/<vod>/layout.json; facecam_box de config manda siempre.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.render import (detect_vod_facecam, load_vod_layout,
                              save_vod_layout)


class TestVodLayoutIO(unittest.TestCase):
    def test_guardar_y_cargar(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = {"vod_id": "v1", "facecam_box": [120, 10, 30, 20],
                    "method": "detected", "samples": 5, "hits": 4,
                    "source": "video"}
            save_vod_layout(tmp, data)
            self.assertTrue(os.path.isfile(
                os.path.join(tmp, "layout.json")))
            self.assertEqual(load_vod_layout(tmp), data)

    def test_sin_archivo_devuelve_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_vod_layout(tmp))

    def test_json_roto_devuelve_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "layout.json"), "w",
                      encoding="utf-8") as f:
                f.write("{no json")
            self.assertIsNone(load_vod_layout(tmp))


class TestDetectVodFacecam(unittest.TestCase):
    def _fake_classify(self, boxes):
        """classify_frame falso: devuelve pip con la caja dada por
        muestra (o center si no hay)."""
        calls = {"n": 0}

        def fake(g, cfg):
            box = boxes[calls["n"] % len(boxes)]
            calls["n"] += 1
            if box is None:
                return "center", {}, "sin cam"
            return "pip", {"box": list(box)}, "cam en esquina"

        return fake

    def test_caja_estable_en_mayoria_gana(self):
        box = [120, 10, 30, 20]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v.mp4")
            open(path, "wb").close()
            with mock.patch("clipagent.render.gray_frame",
                            return_value=[[0] * 160 for _ in range(90)]), \
                    mock.patch("clipagent.render.classify_frame",
                               self._fake_classify(
                                   [box, box, None, box, [5, 5, 10, 10]])):
                result = detect_vod_facecam(path, 3600, {})
        self.assertEqual(result["facecam_box"], box)
        self.assertEqual(result["method"], "detected")
        self.assertEqual(result["hits"], 3)

    def test_sin_mayoria_no_hay_facecam(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v.mp4")
            open(path, "wb").close()
            with mock.patch("clipagent.render.gray_frame",
                            return_value=[[0] * 160 for _ in range(90)]), \
                    mock.patch("clipagent.render.classify_frame",
                               self._fake_classify(
                                   [[1, 1, 5, 5], [2, 2, 6, 6], None, None,
                                    [3, 3, 7, 7]])):
                result = detect_vod_facecam(path, 3600, {})
        self.assertIsNone(result["facecam_box"])
        self.assertEqual(result["method"], "none")

    def test_facecam_box_de_config_manda_siempre(self):
        with mock.patch("clipagent.render.gray_frame") as gray:
            result = detect_vod_facecam(
                "v.mp4", 3600, {"facecam_box": [100, 80, 30, 20]})
        gray.assert_not_called()
        self.assertEqual(result["facecam_box"], [100, 80, 30, 20])
        self.assertEqual(result["method"], "config")

    def test_sin_video_no_detecta(self):
        result = detect_vod_facecam("/no/existe.mp4", 3600, {})
        self.assertIsNone(result["facecam_box"])
        self.assertEqual(result["method"], "none")


if __name__ == "__main__":
    unittest.main(verbosity=2)
