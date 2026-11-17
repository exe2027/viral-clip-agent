#!/usr/bin/env python3
"""Pruebas de 6.6: Telegram solo informativo, nunca rompe nada.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import notify


class TestNotify(unittest.TestCase):
    def test_sin_configurar_no_hace_nada(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            ok, why = notify.send_telegram("hola")
            self.assertFalse(ok)
            self.assertIn("sin configurar", why)

    def test_envia_con_request_fn(self):
        calls = []

        def fake_request(method, url, headers, body):
            calls.append((method, url, body))
            return 200, {"ok": True, "result": {"message_id": 1}}

        env = {"TELEGRAM_BOT_TOKEN": "TOKEN123",
               "TELEGRAM_CHAT_ID": "999"}
        with mock.patch.dict(os.environ, env, clear=True):
            ok, why = notify.send_telegram("publicado: x", fake_request)
        self.assertTrue(ok, why)
        self.assertEqual(len(calls), 1)
        method, url, body = calls[0]
        self.assertEqual(method, "POST")
        self.assertIn("botTOKEN123/sendMessage", url)
        self.assertEqual(body["chat_id"], "999")
        self.assertIn("publicado: x", body["text"])

    def test_error_de_red_no_levanta(self):
        def boom(method, url, headers, body):
            raise ConnectionError("sin red")

        env = {"TELEGRAM_BOT_TOKEN": "TOKEN123",
               "TELEGRAM_CHAT_ID": "999"}
        with mock.patch.dict(os.environ, env, clear=True):
            ok, why = notify.send_telegram("hola", boom)
        self.assertFalse(ok)
        self.assertIn("sin red", why)

    def test_notify_nunca_levanta(self):
        # Ni sin configurar ni con un request_fn que explota.
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(notify.notify("publicado", "x"))
        env = {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"}

        def boom(method, url, headers, body):
            raise RuntimeError("boom")

        with mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(notify.notify("publicado", "x",
                                          request_fn=boom))

    def test_respuesta_no_ok(self):
        def fake_request(method, url, headers, body):
            return 403, {"ok": False, "description": "Forbidden"}

        env = {"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"}
        with mock.patch.dict(os.environ, env, clear=True):
            ok, why = notify.send_telegram("hola", fake_request)
        self.assertFalse(ok)
        self.assertIn("403", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
