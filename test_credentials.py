#!/usr/bin/env python3
"""Pruebas de 3.1: clipagent/credentials.py, única puerta de acceso
a secretos (.env y tokens/ con permisos 600).
"""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import credentials as creds


class TestCredentialsStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_load_y_permisos(self):
        path = creds.save_token("tiktok", {"access_token": "abc",
                                           "expires_at": 9999999999},
                                self.base)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(
            os.stat(os.path.join(self.base, "tokens")).st_mode), 0o700)
        self.assertEqual(creds.load_token("tiktok", self.base),
                         {"access_token": "abc", "expires_at": 9999999999})
        self.assertTrue(creds.token_file_mode_ok(path))
        self.assertIsNone(creds.load_token("no_existe", self.base))

    def test_delete_token(self):
        creds.save_token("twitch", {"access_token": "x"}, self.base)
        self.assertTrue(creds.delete_token("twitch", self.base))
        self.assertFalse(creds.delete_token("twitch", self.base))
        self.assertIsNone(creds.load_token("twitch", self.base))

    def test_token_expired(self):
        self.assertTrue(creds.token_expired(None))
        self.assertTrue(creds.token_expired({"access_token": "x"}))
        self.assertTrue(creds.token_expired(
            {"access_token": "x", "expires_at": 100}, now=200))
        self.assertFalse(creds.token_expired(
            {"access_token": "x", "expires_at": 9999999999}))
        # Sin expires_at pero con refresh_token: renovable, no vencido.
        self.assertFalse(creds.token_expired(
            {"access_token": "x", "refresh_token": "r"}))

    def test_load_dotenv_no_pisa_entorno(self):
        env_path = os.path.join(self.base, ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("CLAVE_PRUEBA=desde_env\nOTRA=1\n")
        os.environ["CLAVE_PRUEBA"] = "real"
        try:
            self.assertEqual(creds.load_dotenv(env_path), env_path)
            self.assertEqual(os.environ["CLAVE_PRUEBA"], "real")
            self.assertEqual(os.environ.get("OTRA"), "1")
        finally:
            os.environ.pop("CLAVE_PRUEBA", None)
            os.environ.pop("OTRA", None)

    def test_migracion_token_legado(self):
        legacy = os.path.join(self.base, "token.json")
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump({"token": "viejo", "refresh_token": "r"}, f)
        new = creds.migrate_legacy_youtube_token(self.base)
        self.assertEqual(new, creds.token_path("youtube_shorts", self.base))
        self.assertFalse(os.path.exists(legacy))
        self.assertEqual(creds.load_token("youtube_shorts", self.base),
                         {"token": "viejo", "refresh_token": "r"})
        self.assertIsNone(creds.migrate_legacy_youtube_token(self.base))

    def test_ningun_otro_modulo_lee_credenciales(self):
        """Ningún .py fuera de credentials.py abre token.json o
        client_secrets.json directamente. Sí vale pedirle la RUTA
        al módulo credentials (``_creds.client_secrets_path(...)``)
        y comprobar su existencia: el archivo lo toca solo
        credentials.py."""
        root = os.path.dirname(os.path.abspath(__file__))
        offenders = []
        for dirpath, _dirs, files in os.walk(root):
            if ".venv" in dirpath or "__pycache__" in dirpath:
                continue
            for name in files:
                if not name.endswith(".py") or name.startswith("test_"):
                    # Los tests crean fixtures; la regla es para
                    # módulos de producción.
                    continue
                path = os.path.join(dirpath, name)
                if os.path.abspath(path) == os.path.abspath(
                        creds.__file__):
                    continue
                with open(path, encoding="utf-8") as f:
                    for n, line in enumerate(f, 1):
                        if "_creds." in line or "credentials." in line:
                            continue
                        if ("token.json" in line or "client_secrets" in line) \
                                and ("open(" in line or "isfile" in line
                                     or "read_text" in line
                                     or "json.load" in line):
                            offenders.append(f"{path}:{n}:{line.strip()}")
        self.assertEqual(offenders, [])

    def test_publisher_usa_credentials(self):
        import publisher
        pub = publisher.YouTubePublisher()
        self.assertEqual(pub.CLIENT_SECRETS,
                         creds.client_secrets_path())
        self.assertEqual(pub.TOKEN_FILE,
                         creds.token_path("youtube_shorts"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
