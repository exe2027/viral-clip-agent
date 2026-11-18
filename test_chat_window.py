#!/usr/bin/env python3
"""Pruebas de la regla 2026-11-17 23:00: del chat, solo la ventana
alrededor del momento del clip, nunca el chat entero.

- parse_peak_ts: segundos y reloj H:MM:SS / MM:SS.
- chat_window_bounds: [pico-ventana, pico+ventana], inicio recortado a 0.
- download_chat_window: llama a chatdownload con -b/-e (la ventana),
  nunca sin recorte; reutiliza el archivo de ventana si ya es válido.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_clips import (
    chat_window_bounds,
    chat_window_path,
    download_chat_window,
    parse_peak_ts,
)


class TestParsePeakTs(unittest.TestCase):
    def test_segundos(self):
        self.assertEqual(parse_peak_ts("6050"), 6050.0)
        self.assertEqual(parse_peak_ts("6050.5"), 6050.5)

    def test_reloj(self):
        self.assertEqual(parse_peak_ts("1:40:50"), 6050.0)
        self.assertEqual(parse_peak_ts("40:50"), 2450.0)

    def test_invalido(self):
        for bad in ("", "abc", "1:2:3:4", "1:xx", "-5"):
            with self.assertRaises(ValueError, msg=bad):
                parse_peak_ts(bad)


class TestChatWindowBounds(unittest.TestCase):
    def test_ventana_normal(self):
        self.assertEqual(chat_window_bounds(6050, 120), (5930, 6170))

    def test_inicio_recortado_a_cero(self):
        self.assertEqual(chat_window_bounds(30, 120), (0, 150))
        self.assertEqual(chat_window_bounds(0, 120), (0, 120))

    def test_nombre_lleva_la_ventana(self):
        path = chat_window_path("runs/2929847999", 5930, 6170)
        self.assertEqual(os.path.basename(path), "chat_w5930-6170.json")


class TestDownloadChatWindow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.chat_path = os.path.join(self.tmp.name, "chat_w5930-6170.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_cmd(self, **kwargs):
        """Corre download_chat_window con el CLI y run mockeados;
        devuelve el comando capturado (o None si no se llamó)."""
        captured = {}

        def fake_run(cmd, what):
            captured["cmd"] = cmd
            captured["what"] = what

        with (
            mock.patch(
                "auto_clips.twitch_cli",
                return_value="/fake/TwitchDownloaderCLI",
            ),
            mock.patch("auto_clips.run", side_effect=fake_run),
        ):
            download_chat_window(
                "2929847999", self.chat_path, 6050, 120, **kwargs
            )
        return captured.get("cmd")

    def test_baja_solo_la_ventana(self):
        cmd = self._run_cmd()
        self.assertIsNotNone(cmd, "debería llamar al CLI")
        self.assertEqual(cmd[1], "chatdownload")
        self.assertIn("--id", cmd)
        self.assertIn("2929847999", cmd)
        self.assertIn("-b", cmd)
        self.assertIn("5930s", cmd)
        self.assertIn("-e", cmd)
        self.assertIn("6170s", cmd)
        self.assertIn("-E", cmd)
        self.assertIn(self.chat_path, cmd)
        # Sin recorte no hay ventana: -b y -e son obligatorios aquí.
        self.assertGreater(cmd.index("-b"), 0)
        self.assertGreater(cmd.index("-e"), 0)

    def test_reutiliza_ventana_valida(self):
        with open(self.chat_path, "w", encoding="utf-8") as f:
            json.dump({"comments": [], "video": {"id": "2929847999"}}, f)
        cmd = self._run_cmd()
        self.assertIsNone(cmd, "no debería volver a bajarla")

    def test_vuelve_a_bajar_si_es_invalida(self):
        with open(self.chat_path, "w", encoding="utf-8") as f:
            f.write("esto no es un chat")
        cmd = self._run_cmd()
        self.assertIsNotNone(cmd, "debería volver a bajarla")


class TestPeakDryRun(unittest.TestCase):
    """Regresión: el dry-run con --peak no debe reventar por la
    mediana. Con chat de ventana, la mayoría de las ventanas son 0
    (vacías antes del momento): la mediana global era 0, el
    múltiplo quedaba en None y score_clip reventaba al formatearlo
    (TypeError). La mediana es local a la ventana."""

    def test_peak_dry_run_con_chat_de_ventana(self):
        from auto_clips import main

        with tempfile.TemporaryDirectory() as tmp:
            chat_path = os.path.join(tmp, "chat_w5930-6170.json")
            comments = []
            for t in range(5930, 6170, 5):
                comments.append(
                    {
                        "content_offset_seconds": t,
                        "message": {"body": "hola"},
                        "commenter": {"name": f"fondo{t}"},
                    }
                )
            for j in range(25):
                comments.append(
                    {
                        "content_offset_seconds": 6050 + j % 10,
                        "message": {"body": "jajaja qué risa"},
                        "commenter": {"name": f"pico{j}"},
                    }
                )
            with open(chat_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "comments": comments,
                        "video": {"id": "2929847999", "length": 16000},
                    },
                    f,
                )
            argv = [
                "auto_clips.py",
                "2929847999",
                "--peak",
                "6050",
                "--chat-window",
                "120",
                "--lang",
                "es",
                "--dry-run",
                "--chat",
                chat_path,
                "--out",
                os.path.join(tmp, "out"),
            ]
            with mock.patch.object(sys, "argv", argv):
                self.assertEqual(main(), 0)


if __name__ == "__main__":
    unittest.main()
