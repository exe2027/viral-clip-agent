#!/usr/bin/env python3
"""Pruebas de 3.6: renovación automática de tokens antes de vencer.
Si el refresco falla, la plataforma pasa a borrador y se notifica;
ningún clip se pierde.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from clipagent import credentials as creds
from clipagent import refresh


def _fake(responses):
    def request_fn(method, url, headers, body):
        for key, (status, payload) in responses.items():
            if key in url:
                return status, payload
        return 404, {"error": "?"}
    return request_fn


class TestRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _token(self, platform, expires_in_s, **extra):
        data = {"access_token": "viejo",
                "expires_at": int(time.time()) + expires_in_s}
        data.update(extra)
        creds.save_token(platform, data, self.base)
        return data

    def test_refresh_due_solo_si_vence_pronto(self):
        self._token("tiktok", 30 * 24 * 3600)
        self.assertFalse(refresh.refresh_due("tiktok", self.base))
        self._token("tiktok", 2 * 24 * 3600)
        self.assertTrue(refresh.refresh_due("tiktok", self.base))
        self._token("tiktok", -10)
        self.assertTrue(refresh.refresh_due("tiktok", self.base))
        self.assertFalse(refresh.refresh_due("no_existe", self.base))

    def test_youtube_refresh_ok(self):
        with open(os.path.join(self.base, "client_secrets.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "cid", '
                    '"client_secret": "csec"}}')
        self._token("youtube_shorts", 100, refresh_token="ref")
        fn = _fake({"oauth2.googleapis.com/token": (200, {
            "access_token": "nuevo", "expires_in": 3600})})
        ok, msg = refresh.refresh_platform("youtube_shorts", self.base, fn)
        self.assertTrue(ok)
        saved = creds.load_token("youtube_shorts", self.base)
        self.assertEqual(saved["access_token"], "nuevo")
        self.assertEqual(saved["refresh_token"], "ref")

    def test_youtube_refresh_rechazado(self):
        with open(os.path.join(self.base, "client_secrets.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "cid", '
                    '"client_secret": "csec"}}')
        self._token("youtube_shorts", 100, refresh_token="ref")
        fn = _fake({"oauth2.googleapis.com/token": (400, {
            "error": "invalid_grant"})})
        ok, msg = refresh.refresh_platform("youtube_shorts", self.base, fn)
        self.assertFalse(ok)
        self.assertIn("reautorizar", msg)

    def test_tiktok_refresh_ok_y_fallo(self):
        self._token("tiktok", 100, refresh_token="ref")
        fn_ok = _fake({"oauth/token": (200, {"access_token": "nuevo",
                                            "refresh_token": "ref2",
                                            "expires_in": 86400})})
        with mock.patch.dict(os.environ, {"TIKTOK_CLIENT_KEY": "k",
                                          "TIKTOK_CLIENT_SECRET": "s"}):
            ok, _ = refresh.refresh_platform("tiktok", self.base, fn_ok)
        self.assertTrue(ok)
        self._token("tiktok", 100, refresh_token="ref")
        fn_bad = _fake({"oauth/token": (401, {"error": "invalid"})})
        with mock.patch.dict(os.environ, {"TIKTOK_CLIENT_KEY": "k",
                                          "TIKTOK_CLIENT_SECRET": "s"}):
            ok, msg = refresh.refresh_platform("tiktok", self.base, fn_bad)
        self.assertFalse(ok)
        self.assertIn("reautorizar", msg)

    def test_refresh_all_notifica_fallos_y_no_pierde_clips(self):
        self._token("youtube_shorts", 100, refresh_token="ref")
        fn = _fake({"oauth2.googleapis.com/token": (400, {
            "error": "invalid_grant"})})
        with open(os.path.join(self.base, "client_secrets.json"), "w",
                  encoding="utf-8") as f:
            f.write('{"installed": {"client_id": "cid", '
                    '"client_secret": "csec"}}')
        notes = []
        results = refresh.refresh_all(self.base, fn, notify=notes.append)
        self.assertFalse(results["youtube_shorts"][0])
        self.assertEqual(len(notes), 1)
        self.assertIn("borrador", notes[0])
        self.assertIn("cola de reintento", notes[0])
        # El token viejo sigue guardado: el clip no se pierde.
        self.assertIsNotNone(
            creds.load_token("youtube_shorts", self.base))

    def test_facebook_no_se_renueva_solo(self):
        ok, msg = refresh.refresh_platform("facebook_reels", self.base)
        self.assertFalse(ok)
        self.assertIn("no caduca", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
