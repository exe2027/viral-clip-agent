"""`download_twitch_segment`: nunca debe colgarse por colisión.

Regla que se prueba aquí (con mocks): si el archivo de salida
existe pero está vacío (descarga parcial de un intento anterior),
el CLI se invoca con `--collision Overwrite` — sin ese flag,
TwitchDownloaderCLI pregunta si sobrescribir y se cuelga sin
stdin, atascando el reintento (le pasó 2 veces al agente de
recuperación de VOD). Con el flag el CLI nunca pregunta.
"""
import os
import tempfile
import types
import unittest
from unittest import mock

import auto_clips


def _fake_run_ok(cmd, **kwargs):
    """Imita un videodownload correcto: escribe el segmento."""
    out = cmd[cmd.index("-o") + 1]
    with open(out, "wb") as f:
        f.write(b"fake-segment")
    return types.SimpleNamespace(returncode=0, stderr="")


class TestDownloadTwitchSegmentCollision(unittest.TestCase):
    def test_archivo_vacio_invoca_cli_con_collision_overwrite(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return _fake_run_ok(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "seg.mp4")
            open(out, "wb").close()  # existe pero vacío: NO reutilizable
            with mock.patch.object(auto_clips, "twitch_cli",
                                  return_value="/fake/TwitchDownloaderCLI"), \
                    mock.patch.object(auto_clips.subprocess, "run",
                                      fake_run):
                got = auto_clips.download_twitch_segment(
                    "2932415021", 3200, 3248, out)
        self.assertEqual(got, out)
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertIn("--collision", cmd)
        self.assertEqual(cmd[cmd.index("--collision") + 1], "Overwrite")
        # El comando nunca lleva Prompt: el CLI no puede preguntar.
        self.assertNotIn("Prompt", cmd)

    def test_archivo_no_vacio_se_reutiliza_sin_llamar_al_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "seg.mp4")
            with open(out, "wb") as f:
                f.write(b"ya-bajado")
            with mock.patch.object(auto_clips, "twitch_cli",
                                  return_value="/fake/TwitchDownloaderCLI"), \
                    mock.patch.object(auto_clips.subprocess, "run") as run:
                got = auto_clips.download_twitch_segment(
                    "2932415021", 3200, 3248, out)
        self.assertEqual(got, out)
        run.assert_not_called()

    def test_sin_archivo_invoca_cli_con_collision_overwrite(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return _fake_run_ok(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "seg.mp4")
            with mock.patch.object(auto_clips, "twitch_cli",
                                  return_value="/fake/TwitchDownloaderCLI"), \
                    mock.patch.object(auto_clips.subprocess, "run",
                                      fake_run):
                got = auto_clips.download_twitch_segment(
                    "2932415021", 3200, 3248, out)
        self.assertEqual(got, out)
        self.assertEqual(len(calls), 1)
        cmd = calls[0]
        self.assertIn("--collision", cmd)
        self.assertEqual(cmd[cmd.index("--collision") + 1], "Overwrite")


if __name__ == "__main__":
    unittest.main()
