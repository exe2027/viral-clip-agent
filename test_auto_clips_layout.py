"""Flag --layout con "game" (spec §9.1).

El corte VOD decide la plantilla antes de cortar y fija
args.layout="game" cuando la fuente no tiene facecam; el flag
acepta "game" explícito. La decisión en sí está cubierta por
test_template_decision.py y la lectura del manifiesto por
test_vod_fallback.py (clip.get("plantilla")): no hay harness de
una corrida completa de auto_clips para un test de extremo a
extremo.
"""
import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.abspath(__file__))


class TestLayoutFlag(unittest.TestCase):
    def test_help_ofrece_game(self):
        out = subprocess.run(
            [sys.executable, os.path.join(REPO, "auto_clips.py"),
             "--help"], capture_output=True, text=True,
            cwd=REPO, timeout=120)
        self.assertEqual(out.returncode, 0)
        line = [ln for ln in out.stdout.splitlines()
                if "--layout" in ln]
        self.assertTrue(line)
        self.assertIn("game", line[0])

    def test_layout_game_no_es_rechazado(self):
        # --layout game + --help: argparse valida el choice ANTES
        # de procesar --help, así que salir con 0 prueba que
        # "game" es un choice válido (un choice inválido saldría
        # con 2). No se ejecuta la corrida (un --video real
        # colgaría el test en red).
        out = subprocess.run(
            [sys.executable, os.path.join(REPO, "auto_clips.py"),
             "--layout", "game", "--help"],
            capture_output=True, text=True, cwd=REPO, timeout=120)
        self.assertEqual(out.returncode, 0)
        self.assertNotIn("invalid choice", out.stderr)


if __name__ == "__main__":
    unittest.main()
