#!/usr/bin/env python3
"""Pruebas de 1.5: secretos solo por entorno o .env, nunca en config.yaml."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent.config import load_dotenv
from watcher import load_watcher_config


def _sin_twitch_env():
    """Entorno sin credenciales de Twitch (las del runner no cuentan)."""
    env = dict(os.environ)
    env.pop("TWITCH_CLIENT_ID", None)
    env.pop("TWITCH_CLIENT_SECRET", None)
    return env


class TestLoadDotenv(unittest.TestCase):
    def test_carga_sin_pisar_el_entorno(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = os.path.join(tmp, ".env")
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# comentario\nTWITCH_CLIENT_ID=desde_env_file\n"
                        "GEMINI_API_KEY=clave123\n")
            with mock.patch.dict(os.environ,
                                 {"TWITCH_CLIENT_ID": "desde_entorno"},
                                 clear=False):
                load_dotenv(env_path)
                self.assertEqual(os.environ["TWITCH_CLIENT_ID"],
                                 "desde_entorno")
            with mock.patch.dict(os.environ, _sin_twitch_env(), clear=True):
                load_dotenv(env_path)
                self.assertEqual(os.environ["TWITCH_CLIENT_ID"],
                                 "desde_env_file")
                self.assertEqual(os.environ["GEMINI_API_KEY"], "clave123")


class TestWatcherSinSecretosEnConfig(unittest.TestCase):
    def _config_con_secretos(self, tmp):
        path = os.path.join(tmp, "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write("watcher:\n  poll_minutes: 15\n  twitch:\n"
                    "    client_id: DESDE_CONFIG\n"
                    "    client_secret: SECRETO_CONFIG\n")
        return path

    def test_config_con_secretos_se_ignora(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_con_secretos(tmp)
            with mock.patch.dict(os.environ, _sin_twitch_env(), clear=True):
                cfg = load_watcher_config(path)
            self.assertEqual(cfg["twitch_client_id"], "")
            self.assertEqual(cfg["twitch_client_secret"], "")

    def test_entorno_manda(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._config_con_secretos(tmp)
            env = _sin_twitch_env()
            env["TWITCH_CLIENT_ID"] = "id_env"
            env["TWITCH_CLIENT_SECRET"] = "sec_env"
            with mock.patch.dict(os.environ, env, clear=True):
                cfg = load_watcher_config(path)
            self.assertEqual(cfg["twitch_client_id"], "id_env")
            self.assertEqual(cfg["twitch_client_secret"], "sec_env")


class TestGitignoreSecretos(unittest.TestCase):
    def test_archivos_de_secretos_ignorados(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("client_secrets.json", "token.json",
                     "publish_queue.json", "prueba.db", ".env"):
            r = subprocess.run(
                ["git", "check-ignore", "-q", name],
                cwd=here, capture_output=True)
            self.assertEqual(r.returncode, 0, f"{name} no está ignorado")

    def test_env_example_sin_valores(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, ".env.example"),
                  encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                _key, value = line.split("=", 1)
                self.assertEqual(value.strip(), "",
                                 f".env.example trae valor en: {line}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
