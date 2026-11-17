#!/usr/bin/env python3
"""Pruebas de 3.3: `clipagent setup` (plan por plataforma y flujo
OAuth local). Nada de lo que imprime contiene secretos.
"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import setup


class TestSetupPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_plan_cubre_todas_las_plataformas(self):
        p = setup.plan(self.base)
        self.assertEqual(set(p), {"youtube_shorts", "tiktok",
                                  "instagram_reels", "facebook_reels",
                                  "twitch", "kick"})
        for platform, entry in p.items():
            self.assertTrue(entry["steps"], platform)
            self.assertTrue(entry["console_url"].startswith("https://"))
            self.assertTrue(entry["docs_url"].startswith("https://"))
            kinds = {s[0] for s in entry["steps"]}
            self.assertIn("manual", kinds, platform)

    def test_youtube_sin_secrets_no_ofrece_oauth(self):
        p = setup.plan(self.base)
        kinds = [s[0] for s in p["youtube_shorts"]["steps"]]
        self.assertNotIn("auto", kinds)

    def test_plan_text_sin_secretos(self):
        os.environ["TWITCH_CLIENT_SECRET"] = "secreto-de-prueba-123"
        try:
            text = setup.plan_text(self.base)
        finally:
            del os.environ["TWITCH_CLIENT_SECRET"]
        self.assertIn("[A MANO]", text)
        self.assertNotIn("secreto-de-prueba-123", text)

    def test_non_interactive_no_toca_tokens(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = setup.run(self.base, non_interactive=True,
                             output=lambda s: out.write(s + "\n"))
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.base, "tokens")))

    def test_youtube_oauth_sin_client_secrets(self):
        self.assertFalse(setup.run_youtube_oauth(self.base))


class _FakeTokenHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(
            {"access_token": "tok", "refresh_token": "ref",
             "expires_in": 3600}).encode())

    def log_message(self, *args):
        pass


class TestLocalOauth(unittest.TestCase):
    def test_flujo_completo_con_servidor_falso(self):
        token_server = HTTPServer(("127.0.0.1", 0), _FakeTokenHandler)
        port = token_server.server_address[1]
        thread = threading.Thread(target=token_server.handle_request,
                                  daemon=True)
        thread.start()
        token_url = f"http://127.0.0.1:{port}/token"

        captured = {}

        def fake_run(auth_url, token_url_, client_id, client_secret,
                     scope, redirect_port=0, timeout=300):
            return setup.run_local_oauth(
                auth_url, token_url_, client_id, client_secret, scope,
                redirect_port=redirect_port, timeout=timeout)

        out = io.StringIO()
        result = {}

        def run_in_thread():
            with redirect_stdout(out):
                with mock.patch("webbrowser.open", lambda url: None):
                    result["tokens"] = setup.run_local_oauth(
                        "https://example.com/auth", token_url,
                        "cid", "csecret", "scope.test", timeout=10)

        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()
        # La URL impresa lleva el puerto del callback: simular el
        # navegador que vuelve con ?code=.
        import time
        callback_port = None
        for _ in range(100):
            time.sleep(0.1)
            m = re.search(r"127\.0\.0\.1(?:%3A|:)(\d+)", out.getvalue())
            if m:
                callback_port = int(m.group(1))
                break
        self.assertIsNotNone(callback_port, out.getvalue())
        urllib.request.urlopen(
            f"http://127.0.0.1:{callback_port}/?code=CODIGO123",
            timeout=5).read()
        t.join(timeout=15)
        token_server.server_close()
        self.assertEqual(result["tokens"]["access_token"], "tok")
        self.assertEqual(result["tokens"]["refresh_token"], "ref")


if __name__ == "__main__":
    unittest.main(verbosity=2)
