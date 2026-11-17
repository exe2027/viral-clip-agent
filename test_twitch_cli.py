#!/usr/bin/env python3
"""Pruebas de 1.6: la ruta de TwitchDownloaderCLI no es fija.

Precedencia: TWITCH_DL_CLI > config.yaml
(tools.twitch_downloader_cli) > PATH. Sin ninguna, error claro.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import auto_clips
from auto_clips import twitch_cli, twitch_cli_available


def _sin_env():
    env = dict(os.environ)
    env.pop("TWITCH_DL_CLI", None)
    return env


class TestTwitchCli(unittest.TestCase):
    def test_env_manda_sobre_config_y_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "TwitchDownloaderCLI")
            open(cli, "w").close()
            env = _sin_env()
            env["TWITCH_DL_CLI"] = cli
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(auto_clips, "load_config",
                                   return_value={"tools": {
                                       "twitch_downloader_cli": "/no/existe"}}):
                self.assertEqual(twitch_cli(), cli)

    def test_env_a_archivo_inexistente_error_claro(self):
        env = _sin_env()
        env["TWITCH_DL_CLI"] = "/no/existe/TwitchDownloaderCLI"
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as cm:
                twitch_cli()
            self.assertIn("TWITCH_DL_CLI", str(cm.exception))

    def test_config_cuando_no_hay_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = os.path.join(tmp, "TwitchDownloaderCLI")
            open(cli, "w").close()
            with mock.patch.dict(os.environ, _sin_env(), clear=True), \
                 mock.patch.object(auto_clips, "load_config",
                                   return_value={"tools": {
                                       "twitch_downloader_cli": cli}}), \
                 mock.patch.object(auto_clips.shutil, "which",
                                   return_value=None):
                self.assertEqual(twitch_cli(), cli)

    def test_config_a_archivo_inexistente_error_claro(self):
        with mock.patch.dict(os.environ, _sin_env(), clear=True), \
             mock.patch.object(auto_clips, "load_config",
                               return_value={"tools": {
                                   "twitch_downloader_cli": "/no/existe"}}):
            with self.assertRaises(RuntimeError) as cm:
                twitch_cli()
            self.assertIn("tools.twitch_downloader_cli", str(cm.exception))

    def test_path_cuando_no_hay_ni_env_ni_config(self):
        with mock.patch.dict(os.environ, _sin_env(), clear=True), \
             mock.patch.object(auto_clips, "load_config",
                               return_value={"tools": {}}), \
             mock.patch.object(auto_clips.shutil, "which",
                               return_value="/usr/bin/TwitchDownloaderCLI"):
            self.assertEqual(twitch_cli(), "/usr/bin/TwitchDownloaderCLI")

    def test_sin_nada_error_claro_y_available_false(self):
        with mock.patch.dict(os.environ, _sin_env(), clear=True), \
             mock.patch.object(auto_clips, "load_config",
                               return_value={"tools": {}}), \
             mock.patch.object(auto_clips.shutil, "which",
                               return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                twitch_cli()
            self.assertIn("TWITCH_DL_CLI", str(cm.exception))
            self.assertIn("tools.twitch_downloader_cli", str(cm.exception))
            self.assertFalse(twitch_cli_available())

    def test_sin_ruta_fija_en_el_codigo(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "auto_clips.py"),
                  encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("TWITCH_CLI_DEFAULT", src)
        self.assertNotIn("~/workspace/tools/twitchdownloader", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
